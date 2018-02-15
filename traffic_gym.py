import bisect

import pygame
import math
import random
import numpy as np
import sys
from custom_graphics import draw_dashed_line, draw_text
from gym import core

seed = 123
random.seed(seed)
np.random.seed(seed)

# Conversion LANE_W from real world to pixels
# A US highway lane width is 3.7 metres, here 50 pixels
LANE_W = 20  # pixels / 3.7 m, lane width
SCALE = LANE_W / 3.7

colours = {
    'w': (255, 255, 255),
    'k': (000, 000, 000),
    'r': (255, 000, 000),
    'g': (000, 255, 000),
    'm': (255, 000, 255),
    'b': (000, 000, 255),
    'c': (000, 255, 255),
    'y': (255, 255, 000),
}


# Car coordinate system, origin under the centre of the read axis
#
#      ^ y                       (x, y, x., y.)
#      |
#   +--=-------=--+
#   |  | z        |
# -----o-------------->
#   |  |          |    x
#   +--=-------=--+
#      |
#
# Will approximate this as having the rear axis on the back of the car!
#
# Car sizes:
# type    | width [m] | length [m]
# ---------------------------------
# Sedan   |    1.8    |    4.8
# SUV     |    2.0    |    5.3
# Compact |    1.7    |    4.5

class Car:
    def __init__(self, lanes, free_lanes, dt):
        """
        Initialise a sedan on a random lane
        :param lanes: tuple of lanes, with ``min`` and ``max`` y coordinates
        :param dt: temporal updating interval
        """
        self.length = round(4.8 * SCALE)
        self.width = round(1.8 * SCALE)
        self.direction = np.array((1, 0), np.float)
        lane = random.choice(tuple(free_lanes))
        self.position = np.array((
            -self.length,
            lanes[lane]['mid'] - self.width // 2
        ), np.float)
        self.target_speed = (random.randrange(115, 130) - 10 * lane) * 1000 / 3600 * SCALE  # m / s
        self.speed = self.target_speed
        self.dt = dt
        self.colour = colours['c']
        self._braked = False
        self._passing = False
        self.target_lane = self.position[1]

    def draw(self, screen):
        """
        Draw current car on screen with a specific colour
        :param screen: PyGame ``Surface`` where to draw
        """
        x, y = self.position
        rectangle = (int(x), int(y), self.length, self.width)
        pygame.draw.rect(screen, self.colour, rectangle)
        pygame.draw.rect(screen, tuple(c/2 for c in self.colour), rectangle, 4)
        if self._braked: self.colour = colours['g']

    def step(self):  # takes also the parameter action = state temporal derivative
        """
        Update current position, given current velocity and acceleration
        """
        error = 0.01 * round(self.target_lane - self.position[1])
        if error == 0:
            self._passing = False
            self.colour = colours['c']
        self.direction = np.array((1, error)) / np.sqrt(1 + error ** 2)  # function of theta
        self.position += self.speed * self.direction * self.dt  # function of newer speed

    def get_lane_set(self, lanes):
        """
        Returns the set of lanes currently occupied
        :param lanes: tuple of lanes, with ``min`` and ``max`` y coordinates
        :return: busy lanes set
        """
        busy_lanes = set()
        y = self.position[1]
        w = self.width
        for lane_idx, lane in enumerate(lanes):
            if lane['min'] <= y <= lane['max'] or lane['min'] <= y + w <= lane['max']:
                busy_lanes.add(lane_idx)
        return busy_lanes

    @property
    def safe_distance(self):
        factor = random.gauss(1, .03)  # 0.9 Germany, 2 safe
        return self.speed * factor

    @property
    def front(self):
        return int(self.position[0] + self.length)

    @property
    def back(self):
        return int(self.position[0])

    def brake(self, fraction):
        # Maximum braking acceleration, eq. (1) from
        # http://www.tandfonline.com/doi/pdf/10.1080/16484142.2007.9638118
        g, mu = 9.81, 0.9  # gravity and friction coefficient
        if not self._passing:
            acceleration = -fraction * g * mu * SCALE
            self.speed += acceleration * self.dt
            self.colour = colours['y']
            self._braked = True

    def pass_(self):
        if not self._passing:
            self.target_lane = self.position[1] - LANE_W
            self._passing = True
            self.colour = colours['m']
            self._braked = False

    def __gt__(self, other):
        """
        Check if self is in front of other: self.back > other.front
        """
        return self.back > other.front

    def __lt__(self, other):
        """
        Check if self is behind of other: self.front < other.back
        """
        return self.front < other.back

    # def __eq__(self, other):
    #     return self.front >= other.back and self.back <= other.front

    def __sub__(self, other):
        """
        Return the distance between self.back and other.front
        """
        return self.back - other.front

class StatefulEnv(core.Env):

    def __init__(self, display=True, nb_lanes=4, fps=30):

        self.offset = int(1.5 * LANE_W)
        self.screen_size = (80 * LANE_W, nb_lanes * LANE_W + self.offset + LANE_W // 2)
        self.fps = fps  # updates per second
        self.delta_t = 1 / fps  # simulation timing interval
        self.nb_lanes = nb_lanes  # total number of lanes
        self.frame = 0  # frame index
        self.lanes = self.build_lanes(nb_lanes)  # create lanes object, list of dicts
        self.vehicles = None  # vehicles list
        self.traffic_rate = 15  # new cars per second
        self.lane_occupancy = None  # keeps track of what vehicle are in each lane
        self.collision = None  # an accident happened
        self.episode = 0  # episode counter

        self.display = display
        if self.display:  # if display is required
            pygame.init()  # init PyGame
            self.screen = pygame.display.set_mode(self.screen_size)  # set screen size
            self.clock = pygame.time.Clock()  # set up timing

    def build_lanes(self, nb_lanes):
        return tuple(
            {'min': self.offset + n * LANE_W,
             'mid': self.offset + LANE_W / 2 + n * LANE_W,
             'max': self.offset + (n + 1) * LANE_W}
            for n in range(nb_lanes)
        )

    def reset(self):
        # Initialise environment state
        self.frame = 0
        self.vehicles = list()
        self.lane_occupancy = [[] for _ in self.lanes]
        self.episode += 1
        pygame.display.set_caption(f'Traffic simulator, episode {self.episode}')
        state = list()
        objects = list()
        return state, objects

    def step(self, action):

        self.collision = False
        # Free lane beginnings
        # free_lanes = set(range(self.nb_lanes))
        free_lanes = set(range(1, self.nb_lanes))

        # For every vehicle
        #   t <- t + dt
        #   leave or enter lane
        #   remove itself if out of screen
        #   update free lane beginnings
        for v in self.vehicles:
            v.step()
            lanes_occupied = v.get_lane_set(self.lanes)
            # Check for any passing and update lane_occupancy
            for l in range(self.nb_lanes):
                if l in lanes_occupied and v not in self.lane_occupancy[l]:
                    # Enter lane
                    bisect.insort(self.lane_occupancy[l], v)
                elif l not in lanes_occupied and v in self.lane_occupancy[l]:
                    # Leave lane
                    self.lane_occupancy[l].remove(v)
            # Remove from the environment cars outside the screen
            if v.position[0] > self.screen_size[0]:
                self.vehicles.remove(v)
                for l in lanes_occupied: self.lane_occupancy[l].remove(v)
            # Update available lane beginnings
            if v.position[0] < v.safe_distance:  # at most safe_distance ahead
                free_lanes -= lanes_occupied

        # Randomly add vehicles, up to 1 / dt per second
        if random.random() < self.traffic_rate * np.sin(2 * np.pi * self.frame * self.delta_t) * self.delta_t:
            if free_lanes:
                car = Car(self.lanes, free_lanes, self.delta_t)
                self.vehicles.append(car)
                for l in car.get_lane_set(self.lanes):
                    # Prepend the new car to each lane it can be found
                    self.lane_occupancy[l].insert(0, car)

        # Compute distances, therefore brake or pass
        for vehicle_list in self.lane_occupancy:
            for i in range(len(vehicle_list) - 1):
                distance = vehicle_list[i + 1] - vehicle_list[i]
                safe_distance = vehicle_list[i].safe_distance
                if safe_distance > distance > 0:
                    if self._safe(vehicle_list[i]):
                        vehicle_list[i].pass_()
                    else: vehicle_list[i].brake(max(0.005 * safe_distance / distance, 1))
                if distance <= 0:
                    vehicle_list[i].colour = colours['r']
                    # Accident, do something!!!
                    self.collision = vehicle_list[i]

        # default reward if nothing happens
        reward = -0.001
        done = False
        state = list()

        if self.frame >= 10000:
            done = True

        if done:
            print(f'Episode ended, reward: {reward}, t={self.frame}')

        self.frame += 1

        objects = list()
        return state, reward, done, objects

    def _safe(self, v):
        if v.back < v.safe_distance: return False  # Cannot see in the future
        current_lane = v.get_lane_set(self.lanes).pop()
        if current_lane == 0: return False
        # Find car behind me one lane up / left
        target_lane = self.lane_occupancy[current_lane - 1]
        me = bisect.bisect(target_lane, v)
        if me > 0:
            behind = target_lane[me - 1]
            if v - behind < behind.safe_distance: return False
        if me < len(target_lane):
            ahead = target_lane[me]
            if ahead - v < v.safe_distance: return False
        return True

    def render(self, mode='human'):
        if self.display:

            # self._pause()

            # capture the closing window and mouse-button-up event
            for event in pygame.event.get():
                if event.type == pygame.QUIT: sys.exit()
                elif event.type == pygame.MOUSEBUTTONUP: self._pause()

            # measure time elapsed, enforce it to be >= 1/fps
            self.clock.tick(self.fps)

            # clear the screen
            self.screen.fill(colours['k'])

            # draw lanes
            for lane in self.lanes:
                sw = self.screen_size[0]  # screen width
                draw_dashed_line(self.screen, colours['w'], (0, lane['min']), (sw, lane['min']), 3)
                draw_dashed_line(self.screen, colours['w'], (0, lane['max']), (sw, lane['max']), 3)
                draw_dashed_line(self.screen, colours['r'], (0, lane['mid']), (sw, lane['mid']))

            for v in self.vehicles:
                v.draw(self.screen)

            draw_text(self.screen, f'# cars: {len(self.vehicles)}', (10, 2))
            draw_text(self.screen, f'frame #: {self.frame}', (120, 2))

            pygame.display.flip()

            if self.collision: self._pause()

    def _pause(self):
        pause = True
        while pause:
            self.clock.tick(15)
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    sys.exit()
                elif e.type == pygame.MOUSEBUTTONUP:
                    pause = False
