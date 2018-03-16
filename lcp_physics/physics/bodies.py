from functools import reduce
import math

import ode
import pygame

import torch
from torch.autograd import Variable

from .utils import Indices, Params, wrap_variable, polar_to_cart, cart_to_polar, cross_2d

X = Indices.X
Y = Indices.Y
DIM = Params.DIM

Tensor = Params.TENSOR_TYPE


class Body(object):
    def __init__(self, pos, vel=(0, 0, 0), mass=1, restitution=Params.DEFAULT_RESTITUTION,
                 fric_coeff=Params.DEFAULT_FRIC_COEFF, eps=Params.DEFAULT_EPSILON,
                 col=(255, 0, 0), thickness=1):
        self.eps = Variable(Tensor([eps]))
        # rotation & position vectors
        pos = wrap_variable(pos)
        if pos.size(0) == 2:
            self.p = torch.cat([Variable(Tensor(1).zero_()), pos])
        else:
            self.p = pos
        self.rot = self.p[0:1]
        self.pos = self.p[1:]

        # linear and angular velocity vector
        vel = wrap_variable(vel)
        if vel.size(0) == 2:
            self.v = torch.cat([Variable(Tensor(1).zero_()), vel])
        else:
            self.v = vel

        self.mass = wrap_variable(mass)
        self.ang_inertia = self._get_ang_inertia(self.mass)
        # M can change if object rotates, not the case for now
        self.M = Variable(Tensor(len(self.v), len(self.v)).zero_())
        s = [self.ang_inertia.size(0), self.ang_inertia.size(0)]
        self.M[:s[0], :s[1]] = self.ang_inertia
        self.M[s[0]:, s[1]:] = Variable(torch.eye(DIM).type_as(self.M.data)) * self.mass

        self.fric_coeff = wrap_variable(fric_coeff)
        self.restitution = wrap_variable(restitution)
        self.forces = []

        self.col = col
        self.thickness = thickness

        self._create_geom()

    def _create_geom(self):
        raise NotImplementedError

    def _get_ang_inertia(self, mass):
        raise NotImplementedError

    def move(self, dt, update_geom_rotation=True):
        new_p = self.p + self.v * dt
        self.set_p(new_p, update_geom_rotation)

    def set_p(self, new_p, update_geom_rotation=True):
        self.p = new_p
        # Reset memory pointers
        self.rot = self.p[0:1]
        self.pos = self.p[1:]

        self.geom.setPosition([self.pos[0], self.pos[1], 0.0])
        if update_geom_rotation:
            # XXX sign correction
            s = math.sin(-self.rot.data[0] / 2)
            c = math.cos(-self.rot.data[0] / 2)
            quat = [s, 0, 0, c]  # Eq 2.3
            self.geom.setQuaternion(quat)

    def apply_forces(self, t):
        return Variable(Tensor(len(self.v)).zero_()) \
               + sum([f.force(t) for f in self.forces])

    def add_no_collision(self, other):
        self.geom.no_collision.add(other.geom)
        other.geom.no_collision.add(self.geom)

    def add_force(self, f):
        self.forces.append(f)

    def draw(self, screen):
        raise NotImplementedError


class Circle(Body):
    def __init__(self, pos, rad, vel=(0, 0, 0), mass=1, restitution=Params.DEFAULT_RESTITUTION,
                 fric_coeff=Params.DEFAULT_FRIC_COEFF, eps=Params.DEFAULT_EPSILON,
                 col=(255, 0, 0), thickness=1):
        self.rad = wrap_variable(rad)
        super().__init__(pos, vel=vel, mass=mass, restitution=restitution,
                         fric_coeff=fric_coeff, eps=eps, col=col, thickness=thickness)

    def _get_ang_inertia(self, mass):
        return mass * self.rad * self.rad / 2

    def _create_geom(self):
        self.geom = ode.GeomSphere(None, self.rad.data[0] + self.eps.data[0])
        self.geom.setPosition(torch.cat([self.pos.data,
                                         Tensor(1).zero_()]))
        self.geom.no_collision = set()

    def move(self, dt, update_geom_rotation=False):
        super().move(dt, update_geom_rotation=update_geom_rotation)

    def set_p(self, new_p, update_geom_rotation=False):
        super().set_p(new_p, update_geom_rotation=update_geom_rotation)

    def draw(self, screen):
        center = self.pos.data.numpy().astype(int)
        rad = int(self.rad.data[0])
        # draw radius to visualize orientation
        r = pygame.draw.line(screen, (0, 0, 255), center,
                             center + [math.cos(self.rot.data[0]) * rad,
                                       math.sin(self.rot.data[0]) * rad],
                             self.thickness)
        # draw circle
        c = pygame.draw.circle(screen, self.col, center,
                               rad, self.thickness)
        return [c, r]


class Hull(Body):
    """ Body's position will not necessarily match reference point.
        Reference point is used as a world frame reference for setting the position
        of vertices, which maintain the world frame position that was passed in.
        After vertices are positioned in world frame using reference point, centroid
        of hull is calculated and the vertices' representation is adjusted to the
        centroid's frame. Object position is set to centroid.
    """
    def __init__(self, ref_point, vertices, vel=(0, 0, 0), mass=1, restitution=Params.DEFAULT_RESTITUTION,
                 fric_coeff=Params.DEFAULT_FRIC_COEFF, eps=Params.DEFAULT_EPSILON,
                 col=(255, 0, 0), thickness=1):
        # center vertices around centroid
        verts = [wrap_variable(v) for v in vertices]
        assert len(verts) > 2 and self.is_clockwise(verts)
        centroid = self._get_centroid(verts)
        self.verts = [v - centroid for v in verts]
        # center position at centroid
        pos = wrap_variable(ref_point) + centroid
        super().__init__(pos, vel=vel, mass=mass, restitution=restitution,
                         fric_coeff=fric_coeff, eps=eps, col=col, thickness=thickness)

    def _get_ang_inertia(self, mass):
        numerator = 0
        denominator = 0
        for i in range(len(self.verts)):
            v1 = self.verts[i]
            v2 = self.verts[(i+1) % len(self.verts)]
            norm_cross = torch.norm(cross_2d(v2, v1))
            numerator += norm_cross * \
                (torch.dot(v1, v1) + torch.dot(v1, v2) + torch.dot(v2, v2))
            denominator += norm_cross
        return 1 / 6 * mass * numerator / denominator

    def _create_geom(self):
        # find vertex furthest from centroid
        max_rad = max([v.dot(v).data[0] for v in self.verts])
        max_rad = math.sqrt(max_rad)

        # XXX Using sphere with largest vertex ray for broadphase for now
        self.geom = ode.GeomSphere(None, max_rad + self.eps.data[0])
        self.geom.setPosition(torch.cat([self.pos.data,
                                         Tensor(1).zero_()]))
        self.geom.no_collision = set()

    def set_p(self, new_p, update_geom_rotation=False):
        rot = new_p[0] - self.p[0]
        if rot.data[0] != 0:
            self.rotate_verts(rot)
        super().set_p(new_p, update_geom_rotation=update_geom_rotation)

    def move(self, dt, update_geom_rotation=False):
        super().move(dt, update_geom_rotation=update_geom_rotation)

    def rotate_verts(self, rot):
        # TODO Optimize rotation
        for i in range(len(self.verts)):
            r, theta = cart_to_polar(self.verts[i], positive=False)
            self.verts[i] = polar_to_cart(r, theta + rot)

    @staticmethod
    def _get_centroid(verts):
        numerator = 0
        denominator = 0
        for i in range(len(verts)):
            v1 = verts[i]
            v2 = verts[(i + 1) % len(verts)]
            cross = cross_2d(v2, v1)
            numerator += cross * (v1 + v2)
            denominator += cross / 2
        return 1 / 6 * numerator / denominator

    @staticmethod
    def is_clockwise(verts):
        total = 0
        for i in range(len(verts)):
            v1 = verts[i]
            v2 = verts[(i+1) % len(verts)]
            total += ((v2[X] - v1[X]) * (v2[Y] + v1[Y])).data[0]
        return total < 0

    def draw(self, screen, draw_center=True):
        # vertices in global frame
        pts = [v + self.pos for v in self.verts]

        # draw hull
        p = pygame.draw.polygon(screen, self.col, pts, self.thickness)
        # draw center
        if draw_center:
            c = pygame.draw.circle(screen, (0, 0, 255),
                                   self.pos.data.numpy().astype(int), 2)
            return [p, c]
        else:
            return [p]


class Rect(Hull):
    def __init__(self, pos, dims, vel=(0, 0, 0), mass=1, restitution=Params.DEFAULT_RESTITUTION,
                 fric_coeff=Params.DEFAULT_FRIC_COEFF, eps=Params.DEFAULT_EPSILON,
                 col=(255, 0, 0), thickness=1):
        self.dims = wrap_variable(dims)
        pos = wrap_variable(pos)
        half_dims = self.dims / 2
        verts = [
            half_dims,
            half_dims * wrap_variable([-1, 1]),
            -half_dims,
            half_dims * wrap_variable([1, -1]),
        ]
        ref_point = pos[-2:]
        super().__init__(ref_point, verts, vel=vel, mass=mass, restitution=restitution,
                         fric_coeff=fric_coeff, eps=eps, col=col, thickness=thickness)
        if pos.size(0) == 3:
            self.set_p(pos)

    def _get_ang_inertia(self, mass):
        return mass * torch.sum(self.dims ** 2) / 12

    def _create_geom(self):
        self.geom = ode.GeomBox(None, torch.cat([self.dims.data + 2 * self.eps.data[0],
                                                 torch.ones(1).type_as(self.M.data)]))
        self.geom.setPosition(torch.cat([self.pos.data, Tensor(1).zero_()]))
        self.geom.no_collision = set()

    def set_p(self, new_p, update_geom_rotation=True):
        super().set_p(new_p, update_geom_rotation=update_geom_rotation)

    def move(self, dt, update_geom_rotation=True):
        super().move(dt, update_geom_rotation=update_geom_rotation)

    def draw(self, screen):
        p = super().draw(screen, draw_center=False)
        # draw diagonals
        verts = [v + self.pos for v in self.verts]
        l1 = pygame.draw.line(screen, (0, 0, 255), verts[0], verts[2])
        l2 = pygame.draw.line(screen, (0, 0, 255), verts[1], verts[3])
        return [l1, l2] + p


# class Rect(Body):
#     def __init__(self, pos, dims, vel=(0, 0, 0), mass=1, restitution=Params.DEFAULT_RESTITUTION,
#                  fric_coeff=Params.DEFAULT_FRIC_COEFF, eps=Params.DEFAULT_EPSILON,
#                  col=(255, 0, 0), thickness=1):
#         self.dims = wrap_variable(dims)
#         super().__init__(pos, vel=vel, mass=mass, restitution=restitution,
#                          fric_coeff=fric_coeff, eps=eps, col=col, thickness=thickness)
#
#     def _get_ang_inertia(self, mass):
#         return mass * torch.sum(self.dims ** 2) / 12
#
#     def _create_geom(self):
#         self.geom = ode.GeomBox(None, torch.cat([self.dims.data + 2 * self.eps.data[0],
#                                                  torch.ones(1).type_as(self.M.data)]))
#         self.geom.setPosition(torch.cat([self.pos.data, Tensor(1).zero_()]))
#         self.geom.no_collision = set()
#
#     def draw(self, screen):
#         # counter clockwise vertices, p1 is top right, origin center of mass
#         half_dims = self.dims / 2
#         r, theta = cart_to_polar(half_dims)
#         p1 = polar_to_cart(r, self.rot.data[0] + theta[0])
#         p2 = polar_to_cart(r, self.rot.data[0] + math.pi - theta[0])
#         p3 = polar_to_cart(r, self.rot.data[0] + math.pi + theta[0])
#         p4 = polar_to_cart(r, self.rot.data[0] + 2 * math.pi - theta[0])
#
#         # points in global frame
#         pts = [(p1 + self.pos).data.numpy(), (p2 + self.pos).data.numpy(),
#                (p3 + self.pos).data.numpy(), (p4 + self.pos).data.numpy()]
#
#         # draw diagonals
#         l1 = pygame.draw.line(screen, (0, 0, 255), pts[0], pts[2])
#         l2 = pygame.draw.line(screen, (0, 0, 255), pts[1], pts[3])
#         # draw center
#         # c = pygame.draw.circle(screen, (0, 0, 255),
#         #                        self.pos.data.numpy().astype(int), 2)
#
#         # draw rectangle
#         r = pygame.draw.polygon(screen, self.col, pts, self.thickness)
#         return [r, l1, l2]
