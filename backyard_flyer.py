import argparse
import math
import time
from enum import Enum

import numpy as np

from udacidrone import Drone
from udacidrone.connection import MavlinkConnection, WebSocketConnection  # noqa: F401
from udacidrone.messaging import MsgID


class States(Enum):
    MANUAL = 0
    ARMING = 1
    TAKEOFF = 2
    WAYPOINT = 3
    LANDING = 4
    DISARMING = 5

class GeometricShape(Enum):
    SQUARE = 'square'
    TRIANGLE = 'triangle'
    CIRCLE = 'circle'

class BackyardFlyer(Drone):

    def __init__(self, connection, geometric_shape, geometric_size):
        super().__init__(connection)
        self.target_position = np.array([0.0, 0.0, 0.0])
        self.all_waypoints = []
        self.in_mission = True
        self.check_state = {}        

        # initial state
        self.flight_state = States.MANUAL
        # The default drone's target altitude during the flight plan.
        self.target_altitude = 3.0
        # Geometric shape the drone will fly
        self.geometric_shape = geometric_shape
        # Base size of the geometric shape
        self.box_base_size = geometric_size
        # Waypoint distance tolerance
        self.waypoint_tolerance = 0.20

        # Register all your callbacks here
        self.register_callback(MsgID.LOCAL_POSITION, self.local_position_callback)
        self.register_callback(MsgID.LOCAL_VELOCITY, self.velocity_callback)
        self.register_callback(MsgID.STATE, self.state_callback)

    def local_position_callback(self):
        """
        This triggers when `MsgID.LOCAL_POSITION` is received and self.local_position contains new data
        """
        # Listen for the take-off altitude and change state if it is close to the desirade target height.
        if self.flight_state == States.TAKEOFF:
            # z coordinate conversion
            altitude = -1.0 * self.local_position[2]
            # Check if altitude is within 95% of target
            if altitude > 0.95 * self.target_position[2]:
                # Calculate the waypoints of the geometric shape
                self.all_waypoints = self.calculate_waypoints()
                self.waypoint_transition()
        elif self.flight_state == States.WAYPOINT:
            # If normalized distance between x,y positions is less than 1.0m change to
            # the next way point or if no waypoint is left call landing transition. 
            if np.linalg.norm(self.target_position[0:2] - self.local_position[0:2]) < self.waypoint_tolerance:
                # If there is any waypoint left
                if len(self.all_waypoints) > 0:
                    self.waypoint_transition()
                else:
                    if np.linalg.norm(self.local_velocity[0:2]) < 1.0:
                        self.landing_transition()
                        
            
    def velocity_callback(self):
        """
        This triggers when `MsgID.LOCAL_VELOCITY` is received and self.local_velocity contains new data.
        """
        if (self.flight_state == States.LANDING and
            (self.global_position[2] - self.global_home[2]) < 0.1 and
            abs(self.local_position[2]) < 0.05):
                self.disarming_transition()

    def state_callback(self):
        """
        Handle some drone's flight state changes

        This triggers when `MsgID.STATE` is received and self.armed and self.guided contain new data
        """
        if not self.in_mission:
            return
        if self.flight_state == States.MANUAL:
            self.arming_transition()
        elif self.flight_state == States.ARMING:
            if self.armed:
                self.takeoff_transition()
        elif self.flight_state == States.DISARMING:
            if not self.armed:
                self.manual_transition()

    def calculate_box(self):
        """
        Return waypoints to fly a box.
        """
        print("Calculating box waypoints")
        local_waypoints = [
            [self.box_base_size, 0.0, self.target_altitude],
            [self.box_base_size, self.box_base_size, self.target_altitude],
            [0.0, self.box_base_size, self.target_altitude],
            [0.0, 0.0, self.target_altitude]
        ]
        self.waypoint_tolerance = 0.20
        return local_waypoints
    
    def calculate_circle(self):
        """
        Return waypoints to fly a circle.
        """
        print("Calculating circle waypoints")
        # Note: The coordinates are [y, x, z]
        center_x = self.box_base_size
        center_y = 0
        # Calculate the circle waypoints resolution based on radius size
        degree_increment = round(360 / (2 * self.box_base_size))
        local_waypoints = [
            [self.box_base_size * np.sin(np.deg2rad(t)) + center_y, -self.box_base_size * np.cos(np.deg2rad(t)) + center_x, self.target_altitude] for t in range(0, 361, degree_increment) if t < 361
        ]
        local_waypoints.append([0, 0, self.target_altitude])
        self.waypoint_tolerance = 1.0
        return local_waypoints

    def calculate_triangle(self):
        """
        Return waypoints to fly an equilateral triangle.
        """
        print("Calculating triangle waypoints")
        # Note: The coordinates are [y, x, z]
        local_waypoints = [
            [self.box_base_size * np.sin(np.deg2rad(60)), self.box_base_size * np.cos(np.deg2rad(60)), self.target_altitude],
            [0.0, self.box_base_size, self.target_altitude],
            [0.0, 0.0, self.target_altitude]
        ]
        self.waypoint_tolerance = 0.15
        return local_waypoints
    
    def calculate_waypoints(self):
        """
        Generate the waypoints of the geometric shape
        for the drone to fly.
        """
        local_waypoints = None
        if self.geometric_shape == GeometricShape.TRIANGLE:
            local_waypoints = self.calculate_triangle()
        elif self.geometric_shape == GeometricShape.CIRCLE:
            local_waypoints = self.calculate_circle()
        else:
            local_waypoints = self.calculate_box()

        print('All waypoints: %s' % (local_waypoints) )
        return local_waypoints

    def arming_transition(self):
        """
        Handle arming transition actions.
        """
        print("Arming transition")
        # Take control of the drone (Change from Manual to Guided)
        self.take_control()
        # Arm the drone
        self.arm()

        # Set the current location to be the home position.
        self.set_home_position(self.global_position[0],
                               self.global_position[1],
                               self.global_position[2])
        # Transition drone to the ARMING state
        self.flight_state = States.ARMING

    def takeoff_transition(self):
        """
        Handle takeoff transition actions:
        
        1. Set target_position altitude to the target altitude attribute
        2. Command a takeoff to target_altitude
        3. Transition to the TAKEOFF state
        """
        print("Takeoff transition")
        self.target_position[2] = self.target_altitude
        self.takeoff(self.target_altitude)
        self.flight_state = States.TAKEOFF

    def waypoint_transition(self):
        """
        Handle waypoint transition actions:
    
        1. Command the next waypoint position
        2. Transition to WAYPOINT state
        """
        print("Waypoint transition")
        # Get the next way point
        self.target_position = self.all_waypoints.pop(0)
        print('target position', self.target_position)
        self.cmd_position(self.target_position[0], self.target_position[1], self.target_position[2], 0.0)
        self.flight_state = States.WAYPOINT

    def landing_transition(self):
        """
        Handle landing transition actions:
        
        1. Command the drone to land
        2. Transition to the LANDING state
        """
        print("Landing transition")
        self.land()
        self.flight_state = States.LANDING

    def disarming_transition(self):
        """
        Handle disarming transition actions:

        1. Command the drone to disarm
        2. Transition to the DISARMING state
        """
        print("Disarm transition")
        self.disarm()
        self.flight_state = States.DISARMING

    def manual_transition(self):
        """This method is provided
        
        1. Release control of the drone
        2. Stop the connection (and telemetry log)
        3. End the mission
        4. Transition to the MANUAL state
        """
        print("Manual transition")
        self.release_control()
        self.stop()
        self.in_mission = False
        self.flight_state = States.MANUAL

    def start(self):
        """This method is provided
        
        1. Open a log file
        2. Start the drone connection
        3. Close the log file
        """
        print("Creating log file")
        self.start_log("Logs", "NavLog.txt")
        print("Starting connection")
        self.connection.start()
        print("Closing log file")
        self.stop_log()

SIZE_MIN_VAL = 2.0
SIZE_MAX_VAL = 30.0
def size_limited_float_type(arg):
    """ Type function for argparse - limit the size within some predefined bounds """
    try:
        f = float(arg)
    except ValueError:    
        raise argparse.ArgumentTypeError("Must be a floating point number")
    if f < SIZE_MIN_VAL or f > SIZE_MAX_VAL:
        raise argparse.ArgumentTypeError("Argument must be <= " + str(SIZE_MAX_VAL) + " and >= " + str(SIZE_MIN_VAL))
    return f

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="""
            This program takes off a drone, flies it on a predetermined geometric path autonomously and lands it, in a simulated backyard environment.
            The simulator program must be open and running the Backyard Flyer simulation.
        """
    )
    parser.add_argument(
        '-p',
        '--port',
        type=int,
        default=5760,
        help='Port number'
    )
    parser.add_argument(
        '--host',
        type=str,
        default='127.0.0.1',
        help="host address, i.e. '127.0.0.1'"
    )
    parser.add_argument(
        '-g',
        '--geometric-shape',
        dest='geometric_shape',
        type=GeometricShape,
        default=GeometricShape.SQUARE,
        help='The geometric shape the drone will follow. Possible values are circle, triangle and square. The default value is square.'
    )
    parser.add_argument(
        '-s',
        '--size',
        dest='geometric_size',
        type=size_limited_float_type,
        default=10,
        help='The base size, in meters, of the geometric shape the drone will follow. For the square and triangle (which is actually an equilateral triangle), this is the size of the side, for the circle, this is the radius. The minimum allowed value is {} and maximum is {}. The default value is 10.'.format(SIZE_MIN_VAL, SIZE_MAX_VAL)
    )
    args = parser.parse_args()

    conn = MavlinkConnection('tcp:{0}:{1}'.format(args.host, args.port), threaded=False, PX4=False)
    drone = BackyardFlyer(conn, args.geometric_shape, args.geometric_size)
    time.sleep(2)
    drone.start()
