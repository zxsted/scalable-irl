from __future__ import division

import numpy as np

from ...models.base import MDPReward
from ...utils.geometry import edist, anisotropic_distance
from ...utils.geometry import distance_to_segment


__all__ = [
    'SimpleBehaviors',
    'FlowBehaviors',
]


class SimpleBehaviors(MDPReward):

    """ Simple behaviors social navigation reward function

    Reward is a function of features of semantic entities in the world such
    as persons, relations between the persons, obstacles, etc. These yield
    simple behaviors based on the level of social compliance that is taken
    into account when considering these attributes, e.g. polite, sociable, etc

    Reward is represented as a linear combination of these features using
    a set of weights.

    """

    def __init__(self, world, weights, kind='linfa', behavior='polite',
                 scaled=True, anisotropic=False, thresh_p=1.8, thresh_r=1.2,
                 discount=0.95):
        super(SimpleBehaviors, self).__init__(world, kind)

        self._weights = np.asarray(weights)
        assert self._weights.size == self.dim, \
            'weight vector and feature vector dimensions do not match'

        self._scaled = scaled
        self._anisotropic = anisotropic
        self._thresh_r = thresh_r
        self._thresh_p = thresh_p
        self._szone = 0.3  # sociable space
        self._behavior = behavior

        assert 0.0 < discount <= 1.0, 'Discount must be in (0, 1]'
        self._gamma = discount

    def __call__(self, state, action):
        """ Compute the reward, r(state, action) """
        action = np.asarray(action)

        # features including default
        phi = [self._feature_relation_disturbance(action),
               self._feature_social_disturbance(action),
               self._feature_goal_deviation(action)]
        reward = np.dot(phi, self._weights)
        return reward, phi

    @property
    def dim(self):
        """ Dimension of the reward function

        count all class members named '_feature_{x}'

        """
        ffs = [f for f, _ in self.__class__.__dict__.items()]
        dim = sum([f.startswith(self._template) for f in ffs])
        return dim

    # -------------------------------------------------------------
    # internals
    # -------------------------------------------------------------

    def _feature_goal_deviation(self, action):
        """ Action goal deviation

        Goal deviation measured by counts for every time
        a way-point in the action trajectory recedes away from the goal

        """
        dist = []
        for i in range(action.shape[0] - 1):
            dnow = edist(self._world.goal, action[i])
            dnext = edist(self._world.goal, action[i + 1])
            deviation = max((dnext - dnow), 0.0)
            dist.append(deviation * self._gamma**i)
        return sum(dist)

    def _feature_social_disturbance(self, action):
        """ Intrusions into personal spaces

        Count the number of way-points of an action trajectory that intrude
        into a specified personal space of a person

        """
        people = [v for k, v in self._world.persons.items()]
        f = 0.0

        for t, waypoint in enumerate(action):
            closest_person = people[0]
            cdist = edist(closest_person, waypoint)

            for p in people[1:]:
                dist = edist(p, waypoint)
                if dist < cdist:
                    cdist = dist
                    closest_person = p

            boundary = self._thresh_p
            if self._scaled:
                speed = np.hypot(closest_person[2], closest_person[3])
                boundary *= speed
                self._szone *= speed

            if self._behavior == 'sociable':
                if self._anisotropic:
                    ad = anisotropic_distance(closest_person, waypoint, ak=3.0)
                    if cdist < ad and cdist < self._szone and cdist < boundary:
                        f += (boundary - cdist) * self._gamma**t

                    if cdist < ad and cdist > self._szone and cdist < boundary:
                        f += (cdist - boundary) * self._gamma**t
                else:
                    if cdist < self._szone and cdist < boundary:
                        f += (boundary - cdist) * self._gamma**t

                    if cdist > self._szone and cdist < boundary:
                        f += (cdist - boundary) * self._gamma**t
            else:
                if self._anisotropic:
                    ad = anisotropic_distance(closest_person, waypoint, ak=3.0)
                    if cdist < ad and cdist < boundary:
                        f += (boundary - cdist) * self._gamma**t
                else:
                    if cdist < boundary:
                        f += (boundary - cdist) * self._gamma**t
        return f

    def _feature_relation_disturbance(self, action):
        """ Intrusions into pair-wise relations

        Count the number of way-points that intrude in the space induced by
        pair-wise relation between persons. The space induced is modeled
        by a rectangle

        """
        f = 0.0
        for t, waypoint in enumerate(action):
            for [i, j] in self._world.relations:
                la = (self._world.persons[i][0], self._world.persons[i][1])
                le = (self._world.persons[j][0], self._world.persons[j][1])

                dist, inside = distance_to_segment(waypoint, la, le)
                if inside and dist < self._thresh_r:
                    f += (self._thresh_r - dist) * self._gamma**t

        return f


############################################################################


class FlowBehaviors(MDPReward):

    """ Reward function for interacting with flows for persons

    This reward function encapsulates the various trade-offs encountered while
    navigating in a dense flows such as hallways, intersections, etc. The
    resulting behaviors include; merging with flow with similar bearing, moving
    in the slipstream of persons, etc

    Reward function is represented as a linear combination of features, whose
    importances are governed by weights.

    """

    def __init__(self, world, weights, discount, radius=1.2, kind='linfa'):
        super(FlowBehaviors, self).__init__(world, kind)
        self._weights = weights
        assert self._weights.size == self.dim, \
            'weight vector and feature vector dimensions do not match'
        assert 0.0 < discount <= 1.0, 'Discount must be in (0, 1]'
        self._gamma = discount
        self._radius = radius
        self._hzone = 0.55

    def __call__(self, state, action):
        density = self._feature_density(action)
        rel_bearing = 0.0
        if density > 0:
            rel_bearing = self._feature_relative_bearing(action)

        phi = [density, rel_bearing,
               self._feature_goal_deviation(action),
               self._feature_goal_distance(action)]
        reward = np.dot(phi, self._weights)
        return reward, phi

    @property
    def dim(self):
        """ Dimension of the reward function """
        # - count all class members named '_feature_{x}'
        ffs = [f for f, _ in self.__class__.__dict__.items()]
        dim = sum([f.startswith(self._template) for f in ffs])
        return dim

    # -------------------------------------------------------------
    # internals
    # -------------------------------------------------------------

    def _feature_goal_deviation(self, action):
        # TODO - change to theta/angles
        dist = []
        for i in range(action.shape[0] - 1):
            dnow = edist(self._world.goal, action[i])
            dnext = edist(self._world.goal, action[i + 1])
            dist.append(max((dnext - dnow) * self._gamma ** i, 0))
        return sum(dist)

    def _feature_goal_distance(self, action):
        phi = 0
        for i, wp in enumerate(action):
            phi += edist(wp, self._world.goal) * self._gamma**i
        return phi

    def _feature_density(self, action):
        phi_d = []
        for t, wp in enumerate(action):
            density = 0
            for _, p in self._world.persons.items():
                dist = edist(wp, p[0:2])
                if dist < self._radius:
                    density += 1 * self._gamma**t
            phi_d.append(density)

        return sum(phi_d)

    def _feature_relative_bearing(self, action):
        phi_f = []
        for t, wp in enumerate(action):
            density = 0
            flow = 0
            for _, p in self._world.persons.items():
                dist = edist(wp, p[0:2])
                if dist < self._radius:
                    density += 1
                    flow += self._goal_orientation(p) * self._gamma**t

            if density > 0:
                flow = flow / density

            phi_f.append(flow)

        return sum(phi_f)

    def _goal_orientation(self, person):
        """ Compute a measure of how close a person will be to the goal in
        the next time step using constant velocity model.
        """
        v = np.hypot(person[3], person[2])
        pnext = (person[0] + v * person[2], person[1] + v * person[3])
        dnow = edist(self._goal, person)
        dnext = edist(self._goal, pnext)
        return dnext - dnow
