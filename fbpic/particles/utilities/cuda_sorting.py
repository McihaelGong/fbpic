# Copyright 2016, FBPIC contributors
# Authors: Remi Lehe, Manuel Kirchen
# License: 3-Clause-BSD-LBNL
"""
This file is part of the Fourier-Bessel Particle-In-Cell code (FB-PIC)
It defines the particle sorting methods on the GPU using CUDA.
"""
from numba import cuda
from pyculib import sorting
import math
import numpy as np

# -----------------------------------------------------
# Sorting utilities - get_cell_idx / sort / prefix_sum
# -----------------------------------------------------

@cuda.jit
def get_cell_idx_per_particle(cell_idx, sorted_idx,
                              x, y, z,
                              invdz, zmin, Nz,
                              invdr, rmin, Nr):
    """
    Get the cell index of each particle.
    The cell index is 1d and calculated by:
    cell index in z + cell index in r * number of cells in z.
    The cell_idx of a particle is defined by
    the lower cell in r and z, that it deposits its field to.

    Parameters
    ----------
    cell_idx : 1darray of integers
        The cell index of the particle

    sorted_idx : 1darray of integers
        The sorted index array needs to be reset
        before doing the sort

    x, y, z : 1darray of floats (in meters)
        The position of the particles
        (is modified by this function)

    invdz, invdr : float (in meters^-1)
        Inverse of the grid step along the considered direction

    zmin, rmin : float (in meters)
        Position of the edge of the simulation box, in each direction

    Nz, Nr : int
        Number of gridpoints along the considered direction
    """
    i = cuda.grid(1)
    if i < cell_idx.shape[0]:
            # Preliminary arrays for the cylindrical conversion
            xj = x[i]
            yj = y[i]
            zj = z[i]
            rj = math.sqrt( xj**2 + yj**2 )

            # Positions of the particles, in the cell unit
            r_cell =  invdr*(rj - rmin) - 0.5
            z_cell =  invdz*(zj - zmin) - 0.5

            # Original index of the uppper and lower cell
            ir_lower = int(math.floor( r_cell ))
            iz_lower = int(math.floor( z_cell ))

            # Treat the boundary conditions
            # guard cells in lower r
            if ir_lower < 0:
                ir_lower = 0
            # absorbing in upper r
            if ir_lower > Nr-1:
                ir_lower = Nr-1
            # periodic boundaries in z
            if iz_lower < 0:
                iz_lower += Nz
            if iz_lower > Nz-1:
                iz_lower -= Nz

            # Reset sorted_idx array
            sorted_idx[i] = i
            # Calculate the 1D cell_idx by cell_idx_ir + cell_idx_iz * Nr
            cell_idx[i] = ir_lower + iz_lower * Nr

def sort_particles_per_cell(cell_idx, sorted_idx):
    """
    Sort the cell index of the particles and
    modify the sorted index array accordingly.

    Parameters
    ----------
    cell_idx : 1darray of integers
        The cell index of the particle

    sorted_idx : 1darray of integers
        Represents the original index of the
        particle before the sorting.
    """
    Ntot = cell_idx.shape[0]
    if Ntot > 0:
        sorter = sorting.RadixSort(Ntot, dtype = np.int32)
        sorter.sort(cell_idx, vals = sorted_idx)

@cuda.jit
def incl_prefix_sum(cell_idx, prefix_sum):
    """
    Perform an inclusive parallel prefix sum on the sorted
    cell index array. The prefix sum array represents the
    cumulative sum of the number of particles per cell
    for each cell index.

    Parameters
    ----------
    cell_idx : 1darray of integers
        The cell index of the particle

    prefix_sum : 1darray of integers
        Represents the cumulative sum of
        the particles per cell
    """
    # i is the index of the macroparticle
    i = cuda.grid(1)
    if i < cell_idx.shape[0]-1:
        # ci: index of the cell of the present macroparticle
        ci = cell_idx[i]
        # ci_next: index of the cell of the next macroparticle
        ci_next = cell_idx[i+1]
        # Fill all the cells between ci and ci_next with the
        # inclusive cumulative sum of the number particles until ci
        while ci < ci_next:
            # The cumulative sum of the number of particle per cell
            # until ci is i+1 (since i obeys python index, starting at 0)
            prefix_sum[ci] = i+1
            ci += 1


@cuda.jit
def prefill_prefix_sum(cell_idx, prefix_sum, Ntot):
    """
    Prefill the prefix sum array so that:
        - the cells that have a lower index than the cell that contains
        the first particle are set to 0
        - the cells that have a higher index than the cell that contains
        the last particle are set to the total number of particles (Ntot)

    All the cells in between will have their value set by `incl_prefix_sum`

    Parameters
    ----------
    cell_idx : 1darray of integers
        The cell index of the particles
    prefix_sum : 1darray of integers
        Represents the cumulative sum of
        the particles per cell
    Ntot: int
        The total number of particles in the current species
    """
    # One thread per cell
    i = cuda.grid(1)
    if i < prefix_sum.shape[0]:
        if Ntot > 0:
            # Fill the first cells with 0
            if i < cell_idx[0]:
                prefix_sum[i] = 0
            # Fill the last cells with Ntot
            elif i >= cell_idx[Ntot-1]:
                prefix_sum[i] = Ntot
        else:
            # If this species has no particles, fill all cells with 0
            prefix_sum[i] = 0

@cuda.jit
def write_sorting_buffer(sorted_idx, val, buf):
    """
    Writes the values of a particle array to a buffer,
    while rearranging them to match the sorted cell index array.

    Parameters
    ----------
    sorted_idx : 1darray of integers
        Represents the original index of the
        particle before the sorting

    val : 1d array of floats
        A particle data array

    buf : 1d array of floats
        A buffer array to temporarily store the
        sorted particle data array
    """
    i = cuda.grid(1)
    if i < val.shape[0]:
        buf[i] = val[sorted_idx[i]]
