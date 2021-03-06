import contextlib
import sys

import torch
from torch import autograd

from pylego.misc import LinearDecay

from .arch import Discriminator, Generator
from ..baseadv import BaseAdversarial

sys.path.append('..')
import gait
import utils


def cosine_kernel(degree=2):
    return lambda x, y: gait.generic_kernel(x, y, lambda u, v: utils.min_clamp_prob(
        ((gait.cosine_similarity(u, v) + 1) / 2) ** degree))


def gaussian_kernel(sigma):
    return lambda x, y: gait.generic_kernel(x, y, lambda u, v: gait.rbf_kernel(u, v, sigmas=[sigma], log=True))


class SimilarityCostModel(BaseAdversarial):

    def __init__(self, flags, *args, **kwargs):
        generator = Generator()
        discriminator = Discriminator()
        super().__init__(flags, generator, discriminator, *args, **kwargs)
        if flags.unbiased:
            self.batch_size = flags.batch_size // 2
        else:
            self.batch_size = flags.batch_size
        uniform = torch.ones(1, self.batch_size, device=self.device)
        self.uniform = uniform / uniform.sum()
        if flags.kernel == 'cosine':
            self.kernel = cosine_kernel(flags.kernel_degree)
        elif flags.kernel == 'gaussian':
            self.sigma_decay = LinearDecay(flags.sigma_decay_start, flags.sigma_decay_end, flags.kernel_initial_sigma,
                                           flags.kernel_sigma)

    def loss_function(self, forward_ret, labels=None):
        x_gen, x_real = forward_ret
        if self.debug:
            debug_context = autograd.detect_anomaly()
        else:
            debug_context = contextlib.nullcontext()
        with debug_context:
            v_real = self.disc(x_real)
            v_gen = self.disc(x_gen)

        if self.flags.kernel == 'gaussian':
            sigma = self.sigma_decay.get_y(self.get_train_steps())
            self.kernel = gaussian_kernel(sigma)
            D = lambda x, y: gait.breg_mixture_divergence_stable(self.uniform, x, self.uniform, y, self.kernel,
                                                                  symmetric=self.flags.symmetric)
        else:
            D = lambda x, y: gait.breg_mixture_divergence(self.uniform, x, self.uniform, y, self.kernel,
                                                           symmetric=self.flags.symmetric)

        if not self.flags.unbiased:
            div = D(v_real, v_gen)
        else:
            x_prime = v_real[:self.batch_size]
            x = v_real[self.batch_size:]
            y_prime = v_gen[:self.batch_size]
            y = v_gen[self.batch_size:]
            div = D(x, y) + D(x, y_prime) + D(x_prime, y) + \
                D(x_prime, y_prime) - 2 * D(y, y_prime) - 2 * D(x, x_prime)

        if self.train_disc():
            loss = -div + self.flags.gp * self.gradient_penalty(x_real, x_gen)
            self.d_loss = loss.item()
        else:
            loss = div
            self.g_loss = loss.item()

        return loss, self.g_loss, self.d_loss
