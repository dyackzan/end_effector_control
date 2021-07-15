#!/usr/bin/env python

import sys
import copy
import rospy
import math
import numpy as np
from math import pi
import moveit_commander
import actionlib
from franka_gripper.msg import MoveGoal, MoveAction
import moveit_msgs.msg
import geometry_msgs.msg
from geometry_msgs.msg import Point, PoseStamped
from moveit_msgs.msg import Grasp, GripperTranslation, PlaceLocation, MoveItErrorCodes
from trajectory_msgs.msg import JointTrajectoryPoint, JointTrajectory
from std_msgs.msg import String, Bool
from moveit_commander.conversions import pose_to_list
from scipy.spatial.transform import Rotation as R
from scipy.interpolate import interp1d
from display_marker_publisher import marker_pub
from tf.transformations import quaternion_from_euler
from collections import deque


class DemoInterface(object):
    """Demo Interface"""
    def __init__(self, real=False):
        super(DemoInterface, self).__init__()
        moveit_commander.roscpp_initialize(sys.argv)
        rospy.init_node('demo_interface', anonymous=True)
        self.robot = moveit_commander.RobotCommander()
        self.scene = moveit_commander.PlanningSceneInterface()
        self.group_name = "panda_arm"
        self.move_group = moveit_commander.MoveGroupCommander(self.group_name)
        self.move_group.set_planner_id("RRTkConfigDefault")
        self.move_group.set_end_effector_link("panda_hand")
        self.display_trajectory_publisher = rospy.Publisher('/move_group/display_planned_path',
                                                   moveit_msgs.msg.DisplayTrajectory,
                                                   queue_size=1)
        self.scene_pub = rospy.Publisher('/move_group/monitored_planning_scene',
                                    moveit_msgs.msg.PlanningScene,
                                    queue_size=1)
        # create a success topic publisher
        # self.success_pub = rospy.Publisher('/point_follow_status', )
        # create client and variable for gripper actions
        self.real = real
        if self.real:
            gripper_client = actionlib.SimpleActionClient('/franka_gripper/move', MoveAction)
            gripper_client.wait_for_server()
            close_goal = MoveGoal(width = 0.045, speed = 0.08)
            open_goal = MoveGoal(width = 0.08, speed = 0.08)
        # create boolean to track if we are ready to move to next trajectory
        self.ready_to_move = True
        self.point_que = deque()


    def all_close(self, goal, actual, tolerance):
        """
        Convenience method for testing if a list of values are within a tolerance of their counterparts in another list
        @param: goal       A list of floats, a Pose or a PoseStamped
        @param: actual     A list of floats, a Pose or a PoseStamped
        @param: tolerance  A float
        @returns: bool
        """
        all_equal = True
        if type(goal) is list:
            for index in range(len(goal)):
                if abs(actual[index] - goal[index]) > tolerance:
                    return False
        elif type(goal) is geometry_msgs.msg.PoseStamped:
            return self.all_close(goal.pose, actual.pose, tolerance)
        elif type(goal) is geometry_msgs.msg.Pose:
            return self.all_close(pose_to_list(goal), pose_to_list(actual), tolerance)
        return True

    def open_gripper(self, wait=True):
        gripper_client.send_goal(open_goal)
        gripper_client.wait_for_result(rospy.Duration.from_sec(5.0))

    def close_gripper(self, wait=True):
        gripper_client.send_goal(close_goal)
        gripper_client.wait_for_result(rospy.Duration.from_sec(5.0))

    def go_to_start(self, wait=True):
        move_group = self.move_group
        joint_goal = move_group.get_current_joint_values()
        rospy.loginfo(joint_goal)
        joint_goal[0] = 0
        joint_goal[1] = -0.785
        joint_goal[2] = 0
        joint_goal[3] = -2.356
        joint_goal[4] = 0
        joint_goal[5] = 1.571
        joint_goal[6] = 0.785
        # setting wait = False allows for dynamic trajectory planning
        move_group.go(joint_goal, wait)
        move_group.stop()
        if self.real:
            self.open_gripper()
        current_joints = move_group.get_current_joint_values()
        return self.all_close(joint_goal, current_joints, 0.01)

    def go_to_pose_goal(self, rpy):
        move_group = self.move_group
        print(move_group.get_current_pose())
        print(move_group.get_current_rpy())
        pose_goal = geometry_msgs.msg.Pose()
        current_pose = move_group.get_current_pose()
        rpy_rot = R.from_euler('xyz', rpy, degrees=False)
        quat = rpy_rot.as_quat()
        print(quat)
        pose_goal.position.x = current_pose.pose.position.x
        pose_goal.position.y = current_pose.pose.position.y
        pose_goal.position.z = current_pose.pose.position.z
        pose_goal.orientation.x = quat[0]
        pose_goal.orientation.y = quat[1]
        pose_goal.orientation.z = quat[2]
        pose_goal.orientation.w = quat[3]
        move_group.set_pose_target(pose_goal)
        plan = move_group.go(wait=True)
        move_group.stop()
        move_group.clear_pose_targets()
        current_pose = self.move_group.get_current_pose().pose
        print(move_group.get_current_rpy())
        return self.all_close(pose_goal, current_pose, 0.01)

    def go_to_rpy_goal(self, rpy):
        move_group = self.move_group
        print(move_group.get_current_pose())
        # start_rpy = [3.1415926535848926, -4.896669808048392e-12, -0.785000000006922]
        move_group.set_rpy_target(rpy)
        plan = move_group.go(wait=True)
        move_group.stop()
        move_group.clear_pose_targets()
        current_pose = self.move_group.get_current_pose().pose
        print("printing current pose")
        print(self.move_group.get_current_pose().pose)
        print("printing current rpy")
        rpy_new = self.move_group.get_current_rpy()
        for num,val in enumerate(rpy_new):
            rpy_new[num] = math.degrees(val)
        print(rpy_new)

    def follow_point(self, point, grasp=False, wait=True):
        # Adding object as object so we don't hit it as we approach
        self.ready_to_move = False
        move_group = self.move_group
        self.publish_object(point, (0.015,0.015,0.03))
        x = point.x
        y = point.y
        z = point.z
        pose_goal = geometry_msgs.msg.Pose()
        if grasp:
            theta = 90
            rpy_rot = R.from_euler('y', theta, degrees=True)
        else:
            theta = self.get_angle(z)
            rpy_rot = R.from_euler('y', theta, degrees=True) * R.from_euler('x', 180, degrees=True)
        pose_goal.position.x = x
        pose_goal.position.y = y
        pose_goal.position.z = z
        print("Theta: %s" % theta)
        print("X motion: %s" % pose_goal.position.x)
        print("Y motion: %s" % pose_goal.position.y)
        print("Z motion: %s" % pose_goal.position.z)
        print("RPY Values: %s" % rpy_rot.as_euler('xyz', degrees=False))
        quat = rpy_rot.as_quat()
        pose_goal.orientation.x = quat[0]
        pose_goal.orientation.y = quat[1]
        pose_goal.orientation.z = quat[2]
        pose_goal.orientation.w = quat[3]
        move_group.set_pose_target(pose_goal)
        plan = move_group.go(wait=wait)
        move_group.stop()
        self.ready_to_move = True
        move_group.clear_pose_targets()
        current_pose = move_group.get_current_pose().pose
        return self.all_close(pose_goal, current_pose, 0.01)


    def get_angle(self, height):
        heights = np.linspace(0, 1, num=20, endpoint=True)
        angles = [-(45 + 90*h) for h in heights]
        f = interp1d(heights, angles)
        return float(f(height))

    def display_point(self, point):
        marker_publisher = marker_pub()
        marker_publisher.display_marker(point)

    def publish_object(self, point, size):
        self.scene.remove_world_object("object")
        rospy.sleep(1)
        object_pose = PoseStamped()
        object_pose.header.frame_id = self.robot.get_planning_frame()
        print("Planning frame: %s" %self.robot.get_planning_frame())
        object_pose.pose.position.x = point.x
        object_pose.pose.position.y = point.y
        object_pose.pose.position.z = point.z
        object_pose.pose.orientation.x = 0.0
        object_pose.pose.orientation.y = 0.0
        object_pose.pose.orientation.z = 0.0
        object_pose.pose.orientation.w = 1.0
        print("object pose: \n %s" % object_pose)
        self.scene.add_box("object", object_pose, size=size)

    def manage_buffer(self, point):
        if len(self.point_que) > 5:
            self.point_que.clear()
            self.follow_point(point)
        else:
            self.point_que.append(point)


    def listen_for_point(self):
        rospy.Subscriber("/point_command", Point, self.manage_buffer)
        rospy.spin()
