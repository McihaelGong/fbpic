# Copyright 2017, FBPIC contributors
# Authors: Remi Lehe, Manuel Kirchen
# License: 3-Clause-BSD-LBNL
"""
This file is part of the Fourier-Bessel Particle-In-Cell code (FB-PIC)
It defines a number of methods that are useful for elementary processes
(e.g. ionization) on CPU and GPU
"""
import numpy as np
from fbpic.threading_utils import njit_parallel, prange
# Check if CUDA is available, then import CUDA functions
from fbpic.cuda_utils import cuda_installed
if cuda_installed:
    from fbpic.cuda_utils import cuda, cuda_tpb_bpg_1d

def allocate_empty( N, use_cuda, dtype ):
    """
    Allocate and return an empty array, of size `N` and type `dtype`,
    either on GPU or CPU, depending on whether `use_cuda` is True or False
    """
    if use_cuda:
        return( cuda.device_array( (N,), dtype=dtype ) )
    else:
        return( np.empty( N, dtype=dtype ) )

def perform_cumsum( input_array ):
    """
    Return an array containing the cumulative sum of the 1darray `input_array`

    (The returned array has one more element than `input_array`; its first
    element is 0 and its last element is the total sum of `input_array`)
    """
    cumulative_array = np.zeros( len(input_array)+1, dtype=np.int64 )
    np.cumsum( input_array, out=cumulative_array[1:] )
    return( cumulative_array )

def reallocate_and_copy_old( species, use_cuda, old_Ntot, new_Ntot ):
    """
    Copy the particle quantities of `species` from arrays of size `old_Ntot`
    into arrays of size `new_Ntot`. Set these arrays as attributes of `species`.

    (The first `old_Ntot` elements of the new arrays are copied from the old
    arrays ; the last elements are left empty and expected to be filled later.)

    When `use_cuda` is True, this function also reallocates
    the sorting buffers for GPU, with a size `new_Ntot`

    Parameters
    ----------
    species: an fbpic Particles object
    use_cuda: bool
        If True, the new arrays are device arrays, and copying is done on GPU.
        If False, the arrays are on CPU, and copying is done on CPU.
    old_Ntot, new_Ntot: int
        Size of the old and new arrays (with old_Ntot < new_Ntot)
    """
    # On GPU, use one thread per particle
    if use_cuda:
        ptcl_grid_1d, ptcl_block_1d = cuda_tpb_bpg_1d( old_Ntot )

    # Iterate over particle attributes and copy the old particles
    for attr in ['x', 'y', 'z', 'ux', 'uy', 'uz', 'w', 'inv_gamma',
                    'Ex', 'Ey', 'Ez', 'Bx', 'By', 'Bz']:
        old_array = getattr(species, attr)
        new_array = allocate_empty( new_Ntot, use_cuda, dtype=np.float64 )
        if use_cuda:
            copy_particle_data_cuda[ ptcl_grid_1d, ptcl_block_1d ](
                old_Ntot, old_array, new_array )
        else:
            copy_particle_data_numba( old_Ntot, old_array, new_array )
        setattr( species, attr, new_array )
    # Copy the tracking id, if needed
    if species.tracker is not None:
        old_array = species.tracker.id
        new_array = allocate_empty( new_Ntot, use_cuda, dtype=np.uint64 )
        if use_cuda:
            copy_particle_data_cuda[ ptcl_grid_1d, ptcl_block_1d ](
                old_Ntot, old_array, new_array )
        else:
            copy_particle_data_numba( old_Ntot, old_array, new_array )
        species.tracker.id = new_array

    # Allocate the auxiliary arrays for GPU
    if use_cuda:
        species.cell_idx = cuda.device_array((new_Ntot,), dtype=np.int32)
        species.sorted_idx = cuda.device_array((new_Ntot,), dtype=np.uint32)
        species.sorting_buffer = cuda.device_array((new_Ntot,), dtype=np.float64)
        if species.n_integer_quantities > 0:
            species.int_sorting_buffer = \
                cuda.device_array( (new_Ntot,), dtype=np.uint64 )

    # Modify the total number of particles
    species.Ntot = new_Ntot


def generate_new_ids( species, old_Ntot, new_Ntot ):
    """
    If `species` is tracked, then generate new ids, between the
    indices `old_Ntot` and `new_Ntot` of the particle ID arrays

    (This is performed either on GPU or CPU.)
    """
    if species.tracker is not None:
        if species.use_cuda:
            species.tracker.generate_new_ids_gpu( old_Ntot, new_Ntot )
        else:
            species.tracker.id[old_Ntot:new_Ntot] = \
                species.tracker.generate_new_ids( new_Ntot - old_Ntot )


@njit_parallel
def copy_particle_data_numba( Ntot, old_array, new_array ):
    """
    Copy the `Ntot` elements of `old_array` into `new_array`, on CPU
    """
    # Loop over single particles (in parallel if threading is enabled)
    for ip in prange( Ntot ):
        new_array[ip] = old_array[ip]
    return( new_array )

if cuda_installed:
    @cuda.jit()
    def copy_particle_data_cuda( Ntot, old_array, new_array ):
        """
        Copy the `Ntot` elements of `old_array` into `new_array`, on GPU
        """
        # Loop over single particles
        ip = cuda.grid(1)
        if ip < Ntot:
            new_array[ip] = old_array[ip]
