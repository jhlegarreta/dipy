"""
=======================
Streamline Registration
=======================

This example shows how to register streamlines into a template space by
applying non-rigid deformations.

At times we will be interested in bringing a set of streamlines into some
common, reference space to compute statistics out of the registered
streamlines. For a discussion on the effects of spatial normalization
approaches on tractography the work by Green et al. [Greene17]_ can be read.

For brevity, we will include in this example only streamlines going through
the corpus callosum connecting left to right superior frontal
cortex. The process of tracking and finding these streamlines is fully
demonstrated in the :ref:`streamline_tools` example. If this example has been
run, we can read the streamlines from file. Otherwise, we'll run that example
first, by importing it. This provides us with all of the variables that were
created in that example.

In order to get the deformation field, we will first use two b0 volumes. The
first one will be the b0 from the Stanford HARDI dataset:

"""

import os.path as op

if not op.exists('lr-superiorfrontal.trk'):
    from streamline_tools import *
else:
    from dipy.data import read_stanford_hardi
    hardi_img, gtab = read_stanford_hardi()
    data = hardi_img.get_data()

"""
The second one will be the template FA map from the HCP1065 dataset:

"""

from dipy.data.fetcher import (fetch_mni_template, read_mni_template)

fetch_mni_template()
img_t2_mni = read_mni_template("a", contrast="T2")

"""
We filter the diffusion data from the Stanford HARDI dataset to find the b0
images.

"""

import numpy as np

b0_idx_stanford = np.where(gtab.b0s_mask)[0]
b0_data_stanford = data[..., b0_idx_stanford]

"""
We then remove the skull from the b0's.

"""

from dipy.segment.mask import median_otsu

b0_masked_stanford, _ = \
    median_otsu(b0_data_stanford,
                vol_idx=[i for i in range(b0_data_stanford.shape[-1])],
                median_radius=4, numpass=4)

"""
And go on to compute the Stanford HARDI dataset mean b0 image.

"""

mean_b0_masked_stanford = np.mean(b0_masked_stanford, axis=3,
                                  dtype=data.dtype)

"""
We use the mean b0 image, along with the HARDI data itself to estimate an FA
image.

"""

from dipy.reconst.dti import TensorModel
from dipy.reconst.dti import fractional_anisotropy

print('Generating simple tensor FA image to use for registrations...')
B0_mask_data = mean_b0_masked_stanford.astype('bool')
hardi_img_affine = hardi_img.affine
model = TensorModel(gtab)
mod = model.fit(data, B0_mask_data)
FA = fractional_anisotropy(mod.evals)
FA[np.isnan(FA)] = 0


"""
We will register the mean b0 to the template FA map non-rigidly to obtain the
deformation field that will be applied to the streamlines. We will first
perform an affine registration to roughly align the two volumes:

"""

from dipy.align.imaffine import (AffineMap, MutualInformationMetric,
                                 AffineRegistration)
from dipy.align.transforms import (TranslationTransform3D, RigidTransform3D,
                                   AffineTransform3D)

static = img_t2_mni.get_data()
static_affine = img_t2_mni.affine
moving = mean_b0_masked_stanford
moving_affine = hardi_img.affine

identity = np.eye(4)
affine_map = AffineMap(identity, static.shape, static_affine, moving.shape,
                       moving_affine)
resampled = affine_map.transform(moving)

"""
We specify the mismatch metric:

"""

nbins = 32
sampling_prop = None
metric = MutualInformationMetric(nbins, sampling_prop)

"""
As well as the optimization strategy:

"""

level_iters = [10, 10, 5]
sigmas = [3.0, 1.0, 0.0]
factors = [4, 2, 1]
affine_reg = AffineRegistration(metric=metric, level_iters=level_iters,
                                sigmas=sigmas, factors=factors)
transform = TranslationTransform3D()

params0 = None
translation = affine_reg.optimize(static, moving, transform, params0,
                                  static_affine, moving_affine)
transformed = translation.transform(moving)
transform = RigidTransform3D()

rigid_map = affine_reg.optimize(static, moving, transform, params0,
                                static_affine, moving_affine,
                                starting_affine=translation.affine)
transformed = rigid_map.transform(moving)
transform = AffineTransform3D()

"""
We bump up the iterations to get a more exact fit:

"""

affine_reg.level_iters = [1000, 1000, 100]
affine_map = affine_reg.optimize(static, moving, transform, params0,
                                 static_affine, moving_affine,
                                 starting_affine=rigid_map.affine)
transformed = affine_map.transform(moving)


"""
We now perform the non-rigid deformation using the Symmetric Diffeomorphic
Registration (SyN) Algorithm proposed by Avants et al. [Avants09]_ (also
implemented in the ANTs software [Avants11]_):

"""

from dipy.align.imwarp import SymmetricDiffeomorphicRegistration
from dipy.align.metrics import CCMetric

metric = CCMetric(3)
level_iters = [10, 10, 5]
sdr = SymmetricDiffeomorphicRegistration(metric, level_iters)

mapping = sdr.optimize(static, moving, static_affine, moving_affine,
                       affine_map.affine)
warped_moving = mapping.transform(moving)
warped_static = mapping.transform_inverse(moving)

"""
We show the registration result with:

"""
from dipy.viz import regtools

regtools.overlay_slices(static, warped_moving, None, 0, "Static", "Moving",
                        "transformed_sagittal.png")
regtools.overlay_slices(static, warped_moving, None, 1, "Static", "Moving",
                        "transformed_coronal.png")
regtools.overlay_slices(static, warped_moving, None, 2, "Static", "Moving",
                        "transformed_axial.png")

"""
.. figure:: transformed_sagittal.png
   :align: center
.. figure:: transformed_coronal.png
   :align: center
.. figure:: transformed_axial.png
   :align: center

   Deformable registration result.
"""

"""
We read the streamlines from file in voxel space:

"""

from dipy.io.streamline import load_trk

streamlines, hdr = load_trk('lr-superiorfrontal.trk')


"""
We then apply the obtained deformation field to the streamlines. We first
apply the non-rigid warping and then apply the computed affine transformation
whose extents must be corrected to account for the different voxel grids of
the moving and static images:

"""

from dipy.tracking.streamline import deform_streamlines, Streamlines

from dipy.tracking.utils import move_streamlines

warped_streamlines = \
    deform_streamlines(streamlines,
                       deform_field=mapping.get_forward_field(),
                       stream_to_current_grid=moving_affine,
                       current_grid_to_world=mapping.codomain_grid2world,
                       stream_to_ref_grid=static_affine,
                       ref_grid_to_world=mapping.domain_grid2world)


adjusted_affine = moving_affine.copy()
adjusted_affine[0][3] = -adjusted_affine[0][3] - static_affine[0][3]
adjusted_affine[1][3] = -adjusted_affine[1][3] + static_affine[1][3]
adjusted_affine[2][3] = -adjusted_affine[2][3] + static_affine[2][3]

rotation, scale = np.linalg.qr(affine_map.affine)
streams = move_streamlines(streamlines, rotation)
scale[0:3, 0:3] = np.dot(scale[0:3, 0:3], np.diag(1. / hdr['voxel_sizes']))
scale[0:3, 3] = abs(scale[0:3, 3])*1. / hdr['voxel_sizes'][0]

affine_streamlines = Streamlines(move_streamlines(warped_streamlines, scale))


"""
We display the original streamlines and the registered streamlines:

"""

from dipy.viz import has_fury

def show_template_bundles(bundles, show=True, fname=None):

    renderer = window.Renderer()
    template_img_data = img_t2_mni.get_data().astype('bool')
    template_actor = actor.contour_from_roi(template_img_data,
                                            color=(50, 50, 50), opacity=0.05)
    renderer.add(template_actor)
    lines_actor = actor.streamtube(bundles, window.colors.orange,
                                   linewidth=0.3)
    renderer.add(lines_actor)
    if show:
        window.show(renderer)
    if fname is not None:
        sleep(1)
        window.record(renderer, n_frames=1, out_path=fname, size=(900, 900))


if has_fury:

    from dipy.viz import window, actor

    from time import sleep

    show_template_bundles(affine_streamlines, show=False,
                          fname='streamline_registration.png')

    """
    .. figure:: streamline_registration.png
       :align: center

       Streamlines before and after registration.

    As it can be seen, the corpus callosum bundles have been deformed to adapt
    to the MNI template space.

    """

"""
Finally, we save the registered streamlines:

"""

from dipy.io.streamline import save_trk

save_trk("warped-lr-superiorfrontal.trk", warped_streamlines,
         affine_map.affine)


"""
References
----------

.. [Avants09] Avants, B. B., Epstein, C. L., Grossman, M., & Gee, J. C. (2009).
   Symmetric Diffeomorphic Image Registration with Cross-Correlation:
   Evaluating Automated Labeling of Elderly and Neurodegenerative Brain, 12(1),
   26-41.

.. [Avants11] Avants, B. B., Tustison, N., & Song, G. (2011). Advanced
   Normalization Tools (ANTS), 1-35.

.. [Greene17] Greene, C., Cieslak, M., & Grafton, S. T. (2017). Effect of
   different spatial normalization approaches on tractography and structural
   brain networks. Network Neuroscience, 1-19.

.. include:: ../links_names.inc

"""
