from __future__ import division

from abc import ABCMeta, abstractmethod
from copy import deepcopy

import numpy as np
from numpy.random import choice, randint

from ...models.base import ModelMixin
from ...utils.common import Logger
from ..mdp_solvers import graph_policy_iteration


########################################################################
# Reward Priors


class RewardPrior(ModelMixin):
    """ Reward prior interface """
    __meta__ = ABCMeta

    def __init__(self, name):
        self.name = name

    @abstractmethod
    def __call__(self, r):
        raise NotImplementedError('Abstract method')

    @abstractmethod
    def log_p(self, r):
        raise NotImplementedError('Abstract method')


class UniformRewardPrior(RewardPrior):
    """ Uniform/flat prior"""
    def __init__(self, name='uniform'):
        super(UniformRewardPrior, self).__init__(name)

    def __call__(self, r):
        rp = np.ones(r.shape[0])
        dist = rp / np.sum(rp)
        return dist

    def log_p(self, r):
        return np.log(self.__call__(r))


class GaussianRewardPrior(RewardPrior):
    """Gaussian reward prior"""
    def __init__(self, name='gaussian', sigma=0.5):
        super(GaussianRewardPrior, self).__init__(name)
        self._sigma = sigma

    def __call__(self, r):
        rp = np.exp(-np.square(r)/(2.0*self._sigma**2)) /\
            np.sqrt(2.0*np.pi)*self._sigma
        return rp / np.sum(rp)

    def log_p(self, r):
        # TODO - make analytical
        return np.log(self.__call__(r))


class LaplacianRewardPrior(RewardPrior):
    """Laplacian reward prior"""
    def __init__(self, name='laplace', sigma=0.5):
        super(LaplacianRewardPrior, self).__init__(name)
        self._sigma = sigma

    def __call__(self, r):
        rp = np.exp(-np.fabs(r)/(2.0*self._sigma)) / (2.0*self._sigma)
        return rp / np.sum(rp)

    def log_p(self, r):
        # TODO - make analytical
        return np.log(self.__call__(r))


########################################################################
# MCMC proposals

class Proposal(ModelMixin):
    """ Proposal for MCMC sampling """
    __meta__ = ABCMeta

    def __init__(self, dim):
        self.dim = dim

    @abstractmethod
    def __call__(self, loc):
        raise NotImplementedError('Abstract class')


class PolicyWalkProposal(Proposal):
    """ PolicyWalk MCMC proposal """
    def __init__(self, dim, delta, bounded=True):
        super(PolicyWalkProposal, self).__init__(dim)
        self.delta = delta
        self.bounded = bounded
        # TODO - allow setting bounds as list of arrays

    def __call__(self, loc):
        new_loc = np.array(loc)
        changed = False
        while not changed:
            d = choice([-self.delta, self.delta])
            i = randint(self.dim)
            if self.bounded:
                if -1 <= new_loc[i]+d <= 1:
                    new_loc[i] += d
                    changed = True
            else:
                new_loc[i] += d
                changed = True
        return new_loc


########################################################################
# BIRL algorithms

class GTBIRL(ModelMixin, Logger):
    """ Generative Trajectory based BIRL algorithm (TBIRL)

    Bayesian Inverse Reinforcement Learning on Adaptive State Graph by
    generation of new trajectories and comparing Q values

    This is an iterative algorithm that improves the reward based on the
    quality differences between expert trajectories and trajectories
    generated by the test rewards

    Parameters
    ----------
    demos : array-like, shape (M x d)
        Expert demonstrations as M trajectories of state action pairs
    mdp : ``GraphMDP`` object
        The underlying (semi) Markov decision problem
    prior : ``RewardPrior`` object
        Reward prior callable
    loss : callable
        Reward loss callable
    max_iter : int, optional (default=10)
        Number of iterations of the TBIRL algorithm
    alpha : float, optional (default=0.9)
        Expert optimality parameter for softmax Boltzman temperature


    Attributes
    -----------
    _demos : array-like, shape (M x d)
        Expert demonstrations as M trajectories of state action pairs
    _prior : ``RewardPrior`` object
        Reward prior callable
    _loss : callable
        Reward loss callable
    _max_iter : int, optional (default=10)
        Number of iterations of the TBIRL algorith
    _beta : float, optional (default=0.9)
        Expert optimality parameter for softmax Boltzman temperature

    """

    __meta__ = ABCMeta

    def __init__(self, demos, cg, prior, loss, beta=0.7, max_iter=10):
        self._demos = demos
        self._prior = prior
        self._rep = cg  # control graph representation
        self._loss = loss
        self._beta = beta
        self._max_iter = max_iter

    def solve(self, persons, relations):
        """ Find the true reward function """
        reward = self.initialize_reward()
        # self._compute_policy(reward=reward)
        # init_g_trajs = self._rep.find_best_policies()

        g_trajs = [deepcopy(self._demos)]

        for iteration in range(self._max_iter):
            # - Compute reward likelihood, find the new reward
            reward = self.find_next_reward(g_trajs)

            # - generate trajectories using current reward and store
            self._compute_policy(reward)
            trajs = self._rep.find_best_policies()
            g_trajs.append(trajs)

            # g_trajs = [trajs]

            self.info('Iteration: {}'.format(iteration))

        return reward

    @abstractmethod
    def find_next_reward(self, g_trajs):
        """ Compute a new reward based on current iteration """
        raise NotImplementedError('Abstract')

    @abstractmethod
    def initialize_reward(self):
        """ Initialize reward function based on sovler """
        raise NotImplementedError('Abstract')

    # -------------------------------------------------------------
    # internals
    # -------------------------------------------------------------

    def _compute_policy(self, reward):
        """ Compute the policy induced by a given reward function """
        self._rep = self._rep.update_rewards(reward)
        graph_policy_iteration(self._rep.graph,
                               self._rep.mdp.gamma)

    def _expert_trajectory_quality(self, reward):
        """ Compute the Q-function of expert trajectories """
        G = self._rep.graph
        gr = 100  # TODO - make configurable
        gamma = self._rep.mdp.gamma

        QEs = []
        for traj in self._demos:
            time = 0
            QE = 0
            for n in traj:
                actions = G.out_edges(n)
                if actions:
                    e = actions[G.gna(n, 'pi')]
                    r = np.dot(reward, G.gea(e[0], e[1], 'phi'))
                    QE += (gamma ** time) * r
                    time += G.gea(e[0], e[1], 'duration')
                else:
                    QE += (gamma ** time) * gr
            QEs.append(QE)
        return QEs

    def _generated_trajectory_quality(self, reward, g_trajs):
        """ Compute the Q-function of generated trajectories """
        G = self._rep.graph
        gr = 100
        gamma = self._rep.mdp.gamma

        QPiv = []
        for g_traj in g_trajs:
            QPis = []
            for traj in g_traj:
                QPi = 0
                time = 0
                for n in traj:
                    actions = G.out_edges(n)
                    if actions:
                        e = actions[G.gna(n, 'pi')]
                        r = np.dot(reward, G.gea(e[0], e[1], 'phi'))
                        QPi += (gamma ** time) * r
                        time += G.gea(e[0], e[1], 'duration')
                    else:
                        QPi += (gamma ** time) * gr
                QPis.append(QPi)
            QPiv.append(QPis)
        return QPiv
