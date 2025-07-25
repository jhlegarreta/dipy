from pathlib import Path
from warnings import warn

import numpy as np

from dipy.align import affine_registration, motion_correction
from dipy.align.imaffine import AffineMap
from dipy.align.imwarp import DiffeomorphicMap, SymmetricDiffeomorphicRegistration
from dipy.align.metrics import CCMetric, EMMetric, SSDMetric
from dipy.align.reslice import reslice
from dipy.align.streamlinear import slr_with_qbx
from dipy.align.streamwarp import bundlewarp
from dipy.core.gradients import gradient_table, mask_non_weighted_bvals
from dipy.io.gradients import read_bvals_bvecs
from dipy.io.image import load_nifti, save_nifti, save_qa_metric
from dipy.io.stateful_tractogram import StatefulTractogram
from dipy.io.streamline import load_tractogram, save_tractogram
from dipy.tracking.streamline import set_number_of_points, transform_streamlines
from dipy.utils.logging import logger
from dipy.utils.optpkg import optional_package
from dipy.workflows.utils import handle_vol_idx
from dipy.workflows.workflow import Workflow

pd, have_pd, _ = optional_package("pandas")


def check_dimensions(static, moving):
    """Check the dimensions of the input images.

    Parameters
    ----------
    static : 2D or 3D array
        the image to be used as reference during optimization.

    moving: 2D or 3D array
        the image to be used as "moving" during optimization. It is
        necessary to pre-align the moving image to ensure its domain
        lies inside the domain of the deformation fields. This is assumed
        to be accomplished by "pre-aligning" the moving image towards the
        static using an affine transformation given by the
        'starting_affine' matrix.

    """
    if len(static.shape) != len(moving.shape):
        raise ValueError(
            "Dimension mismatch: The input images must have same number of dimensions."
        )

    if len(static.shape) > 3 and len(moving.shape) > 3:
        raise ValueError(
            "Dimension mismatch: One of the input should be 2D or 3D dimensions."
        )


class ResliceFlow(Workflow):
    @classmethod
    def get_short_name(cls):
        return "reslice"

    def run(
        self,
        input_files,
        new_vox_size,
        order=1,
        mode="constant",
        cval=0,
        num_processes=1,
        out_dir="",
        out_resliced="resliced.nii.gz",
    ):
        """Reslice data with new voxel resolution defined by ``new_vox_sz``

        Parameters
        ----------
        input_files : string or Path
            Path to the input volumes. This path may contain wildcards to
            process multiple inputs at once.
        new_vox_size : variable float
            new voxel size.
        order : int, optional
            order of interpolation, from 0 to 5, for resampling/reslicing,
            0 nearest interpolation, 1 trilinear etc.. if you don't want any
            smoothing 0 is the option you need.
        mode : string, optional
            Points outside the boundaries of the input are filled according
            to the given mode 'constant', 'nearest', 'reflect' or 'wrap'.
        cval : float, optional
            Value used for points outside the boundaries of the input if
            mode='constant'.
        num_processes : int, optional
            Split the calculation to a pool of children processes. This only
            applies to 4D `data` arrays. Default is 1. If < 0 the maximal
            number of cores minus ``num_processes + 1`` is used (enter -1 to
            use as many cores as possible). 0 raises an error.
        out_dir : string, optional
            Output directory.
        out_resliced : string, optional
            Name of the resliced dataset to be saved.
        """

        io_it = self.get_io_iterator()

        for inputfile, outpfile in io_it:
            data, affine, vox_sz = load_nifti(inputfile, return_voxsize=True)
            logger.info(f"Processing {inputfile}")
            new_data, new_affine = reslice(
                data,
                affine,
                vox_sz,
                new_vox_size,
                order=order,
                mode=mode,
                cval=cval,
                num_processes=num_processes,
            )
            save_nifti(outpfile, new_data, new_affine)
            logger.info(f"Resliced file save in {outpfile}")


class SlrWithQbxFlow(Workflow):
    @classmethod
    def get_short_name(cls):
        return "slrwithqbx"

    def run(
        self,
        static_files,
        moving_files,
        x0="affine",
        rm_small_clusters=50,
        qbx_thr=(40, 30, 20, 15),
        num_threads=None,
        greater_than=50,
        less_than=250,
        nb_pts=20,
        progressive=True,
        bbox_valid_check=True,
        out_dir="",
        out_moved="moved.trx",
        out_affine="affine.txt",
        out_stat_centroids="static_centroids.trx",
        out_moving_centroids="moving_centroids.trx",
        out_moved_centroids="moved_centroids.trx",
    ):
        """Streamline-based linear registration.

        For efficiency we apply the registration on cluster centroids and
        remove small clusters.

        See :footcite:p:`Garyfallidis2014b`, :footcite:p:`Garyfallidis2015`,
        :footcite:p:`Garyfallidis2018` for further details.

        Parameters
        ----------
        static_files : string or Path
            List of reference/fixed bundle tractograms.
        moving_files : string or Path
            List of target bundle tractograms that will be moved/registered to
            match the static bundles.
        x0 : string, optional
            rigid, similarity or affine transformation model.
        rm_small_clusters : int, optional
            Remove clusters that have less than `rm_small_clusters`.
        qbx_thr : variable int, optional
            Thresholds for QuickBundlesX.
        num_threads : int, optional
            Number of threads to be used for OpenMP parallelization. If None
            (default) the value of OMP_NUM_THREADS environment variable is
            used if it is set, otherwise all available threads are used. If
            < 0 the maximal number of threads minus $|num_threads + 1|$ is used
            (enter -1 to use as many threads as possible). 0 raises an error.
            Only metrics using OpenMP will use this variable.
        greater_than : int, optional
            Keep streamlines that have length greater than
            this value.
        less_than : int, optional
            Keep streamlines have length less than this value.
        nb_pts : int, optional
            Number of points for discretizing each streamline.
        progressive : boolean, optional
            True to enable progressive registration.
        bbox_valid_check : boolean, optional
            Verification for negative voxel coordinates or values above the volume
            dimensions.
        out_dir : string, optional
            Output directory.
        out_moved : string, optional
            Filename of moved tractogram.
        out_affine : string, optional
            Filename of affine for SLR transformation.
        out_stat_centroids : string, optional
            Filename of static centroids.
        out_moving_centroids : string, optional
            Filename of moving centroids.
        out_moved_centroids : string, optional
            Filename of moved centroids.

        Notes
        -----
        The order of operations is the following. First short or long
        streamlines are removed. Second the tractogram or a random selection
        of the tractogram is clustered with QuickBundlesX. Then SLR
        :footcite:p:`Garyfallidis2015` is applied.

        References
        ----------
        .. footbibliography::
        """

        io_it = self.get_io_iterator()

        logger.info("QuickBundlesX clustering is in use")
        logger.info(f"QBX thresholds {qbx_thr}")

        for (
            static_file,
            moving_file,
            out_moved_file,
            out_affine_file,
            static_centroids_file,
            moving_centroids_file,
            moved_centroids_file,
        ) in io_it:
            logger.info(f"Loading static file {static_file}")
            logger.info(f"Loading moving file {moving_file}")

            static_obj = load_tractogram(
                static_file, "same", bbox_valid_check=bbox_valid_check
            )
            moving_obj = load_tractogram(
                moving_file, "same", bbox_valid_check=bbox_valid_check
            )

            moved, affine, centroids_static, centroids_moving = slr_with_qbx(
                static_obj.streamlines,
                moving_obj.streamlines,
                x0=x0,
                rm_small_clusters=rm_small_clusters,
                greater_than=greater_than,
                less_than=less_than,
                qbx_thr=qbx_thr,
                progressive=progressive,
                nb_pts=nb_pts,
                num_threads=num_threads,
            )

            logger.info(f"Saving output file {out_moved_file}")

            new_tractogram = StatefulTractogram(
                moved,
                moving_obj,
                moving_obj.space,
            )
            save_tractogram(
                new_tractogram, str(out_moved_file), bbox_valid_check=bbox_valid_check
            )

            logger.info(f"Saving output file {out_affine_file}")
            np.savetxt(out_affine_file, affine)

            logger.info(f"Saving output file {static_centroids_file}")
            new_tractogram = StatefulTractogram(
                centroids_static,
                moving_obj,
                moving_obj.space,
            )
            save_tractogram(
                new_tractogram,
                str(static_centroids_file),
                bbox_valid_check=bbox_valid_check,
            )

            logger.info(f"Saving output file {moving_centroids_file}")
            new_tractogram = StatefulTractogram(
                centroids_moving,
                moving_obj,
                moving_obj.space,
            )
            save_tractogram(
                new_tractogram,
                str(moving_centroids_file),
                bbox_valid_check=bbox_valid_check,
            )

            centroids_moved = transform_streamlines(centroids_moving, affine)

            logger.info(f"Saving output file {moved_centroids_file}")

            new_tractogram = StatefulTractogram(
                centroids_moved,
                moving_obj,
                moving_obj.space,
            )
            save_tractogram(
                new_tractogram,
                str(moved_centroids_file),
                bbox_valid_check=bbox_valid_check,
            )


class ImageRegistrationFlow(Workflow):
    """
    The registration workflow allows the user to use only one type of
    registration (such as center of mass or rigid body registration only).

    Alternatively, a registration can be done in a progressive manner.
    For example, using affine registration with progressive set to 'True'
    will involve center of mass, translation, rigid body and full affine
    registration. Whereas, when progressive is False the registration will
    include only center of mass and affine registration. The progressive
    registration will be slower but will improve the quality.

    This can be controlled by using the progressive flag (True by default).
    """

    def run(
        self,
        static_image_files,
        moving_image_files,
        transform="affine",
        nbins=32,
        sampling_prop=None,
        metric="mi",
        level_iters=(10000, 1000, 100),
        sigmas=(3.0, 1.0, 0.0),
        factors=(4, 2, 1),
        progressive=True,
        save_metric=False,
        static_vol_idx=None,
        moving_vol_idx=None,
        out_dir="",
        out_moved="moved.nii.gz",
        out_affine="affine.txt",
        out_quality="quality_metric.txt",
    ):
        """
        Parameters
        ----------
        static_image_files : string or Path
            Path to the static image file.
        moving_image_files : string or Path
            Path to the moving image file.
        transform : string, optional
            ``'com'``: center of mass; ``'trans'``: translation; ``'rigid'``:
            rigid body; ``'rigid_isoscaling'``: rigid body + isotropic scaling,
            ``'rigid_scaling'``: rigid body + scaling; ``'affine'``: full affine
            including translation, rotation, shearing and scaling.
        nbins : int, optional
            Number of bins to discretize the joint and marginal PDF.
        sampling_prop : int, optional
            Number ([0-100]) of voxels for calculating the PDF. None implies all
            voxels.
        metric : string, optional
            Similarity metric for gathering mutual information.
        level_iters : variable int, optional
            The number of iterations at each scale of the scale space.
            `level_iters[0]` corresponds to the coarsest scale,
            `level_iters[-1]` the finest, where n is the length of the
            sequence.
        sigmas : variable floats, optional
            Custom smoothing parameter to build the scale space (one parameter
            for each scale).
        factors : variable floats, optional
            Custom scale factors to build the scale space (one factor for each scale).
        progressive : boolean, optional
            Enable/Disable the progressive registration.
        save_metric : boolean, optional
            If true, quality assessment metric are saved in
            'quality_metric.txt'.
        static_vol_idx : str, optional
            1D array representing indices of ``axis=-1`` of a 4D
            `static` input volume. From the command line use something like
            `3 4 5 6`. From script use something like `[3, 4, 5, 6]`. This
            input is required for 4D volumes.
        moving_vol_idx : str, optional
            1D array representing indices of ``axis=-1`` of a 4D
            `moving` input volume. From the command line use something like
            `3 4 5 6`. From script use something like `[3, 4, 5, 6]`. This
            input is required for 4D volumes.
        out_dir : string or Path, optional
            Directory to save the transformed image and the affine matrix
        out_moved : string, optional
            Name for the saved transformed image.
        out_affine : string, optional
            Name for the saved affine matrix.
        out_quality : string, optional
            Name of the file containing the saved quality metric.
        """

        io_it = self.get_io_iterator()
        transform = transform.lower()
        metric = metric.upper()
        if metric != "MI":
            raise ValueError("Invalid similarity metric: Please provide avalid metric.")

        if progressive:
            pipeline_opt = {
                "com": ["center_of_mass"],
                "trans": ["center_of_mass", "translation"],
                "rigid": ["center_of_mass", "translation", "rigid"],
                "rigid_isoscaling": [
                    "center_of_mass",
                    "translation",
                    "rigid_isoscaling",
                ],
                "rigid_scaling": ["center_of_mass", "translation", "rigid_scaling"],
                "affine": ["center_of_mass", "translation", "rigid", "affine"],
            }
        else:
            pipeline_opt = {
                "com": ["center_of_mass"],
                "trans": ["center_of_mass", "translation"],
                "rigid": ["center_of_mass", "rigid"],
                "rigid_isoscaling": ["center_of_mass", "rigid_isoscaling"],
                "rigid_scaling": ["center_of_mass", "rigid_scaling"],
                "affine": ["center_of_mass", "affine"],
            }

        static_vol_idx = handle_vol_idx(static_vol_idx)

        moving_vol_idx = handle_vol_idx(moving_vol_idx)

        pipeline = pipeline_opt.get(transform)

        if pipeline is None:
            raise ValueError(
                "Invalid transformation:"
                " Please see program's help"
                " for allowed values of"
                " transformation."
            )

        for static_img, mov_img, moved_file, affine_matrix_file, qual_val_file in io_it:
            # Load the data from the input files and store into objects.
            static, static_grid2world = load_nifti(static_img)
            moving, moving_grid2world = load_nifti(mov_img)

            if static_vol_idx is not None:
                static = static[..., static_vol_idx].mean(axis=-1)
            if moving_vol_idx is not None:
                moving = moving[..., moving_vol_idx].mean(axis=-1)

            check_dimensions(static, moving)

            starting_affine = None

            # If only center of mass is selected do not return metric
            if pipeline == ["center_of_mass"]:
                moved_image, affine_matrix = affine_registration(
                    moving,
                    static,
                    moving_affine=moving_grid2world,
                    static_affine=static_grid2world,
                    pipeline=pipeline,
                    starting_affine=starting_affine,
                    metric=metric,
                    level_iters=level_iters,
                    sigmas=sigmas,
                    factors=factors,
                    nbins=nbins,
                    sampling_proportion=sampling_prop,
                )
            else:
                moved_image, affine_matrix, xopt, fopt = affine_registration(
                    moving,
                    static,
                    moving_affine=moving_grid2world,
                    static_affine=static_grid2world,
                    pipeline=pipeline,
                    starting_affine=starting_affine,
                    metric=metric,
                    level_iters=level_iters,
                    sigmas=sigmas,
                    factors=factors,
                    ret_metric=True,
                    nbins=nbins,
                    sampling_proportion=sampling_prop,
                )

                """
                Saving the moved image file and the affine matrix.
                """
                logger.info(f"Optimal parameters: {str(xopt)}")
                logger.info(f"Similarity metric: {str(fopt)}")

                if save_metric:
                    save_qa_metric(qual_val_file, xopt, fopt)

            save_nifti(moved_file, moved_image, static_grid2world)
            np.savetxt(affine_matrix_file, affine_matrix)


class ApplyTransformFlow(Workflow):
    def run(
        self,
        static_image_files,
        moving_image_files,
        transform_map_file,
        transform_type="affine",
        out_dir="",
        out_file="transformed.nii.gz",
    ):
        """
        Parameters
        ----------
        static_image_files : string or Path
            Path of the static image file.
        moving_image_files : string or Path
            Path of the moving image(s). It can be a single image or a
            folder containing multiple images.
        transform_map_file : string or Path
            For the affine case, it should be a text(``*.txt``) file containing
            the affine matrix. For the diffeomorphic case,
            it should be a nifti file containing the mapping displacement
            field in each voxel with this shape (x, y, z, 3, 2).
        transform_type : string, optional
            Select the transformation type to apply between 'affine' or
            'diffeomorphic'.
        out_dir : string or Path, optional
            Directory to save the transformed files.
        out_file : string, optional
            Name of the transformed file.
            It is recommended to use the flag --mix-names to
            prevent the output files from being overwritten.

        """
        if transform_type.lower() not in ["affine", "diffeomorphic"]:
            raise ValueError(
                "Invalid transformation type: Please"
                " provide a valid transform like 'affine'"
                " or 'diffeomorphic'"
            )

        io = self.get_io_iterator()

        for static_image_file, moving_image_file, transform_file, out_file in io:
            # Loading the image data from the input files into object.
            static_image, static_grid2world = load_nifti(static_image_file)
            moving_image, moving_grid2world = load_nifti(moving_image_file)

            # Doing a sanity check for validating the dimensions of the input
            # images.
            if static_image.ndim > moving_image.ndim:
                static_image = static_image[..., 0]
            if static_image.ndim < moving_image.ndim:
                moving_image_full = moving_image
                moving_image = moving_image[..., 0]
            else:
                moving_image_full = None
            check_dimensions(static_image, moving_image)

            if transform_type.lower() == "affine":
                # Loading the affine matrix.
                affine_matrix = np.loadtxt(transform_file)

                # Setting up the affine transformation object.
                mapping = AffineMap(
                    affine=affine_matrix,
                    domain_grid_shape=static_image.shape,
                    domain_grid2world=static_grid2world,
                    codomain_grid_shape=moving_image.shape,
                    codomain_grid2world=moving_grid2world,
                )

            elif transform_type.lower() == "diffeomorphic":
                # Loading the diffeomorphic map.
                disp_data, disp_affine = load_nifti(transform_file)

                mapping = DiffeomorphicMap(
                    3,
                    disp_data.shape[:3],
                    disp_grid2world=np.linalg.inv(disp_affine),
                    domain_shape=static_image.shape,
                    domain_grid2world=static_grid2world,
                    codomain_shape=moving_image.shape,
                    codomain_grid2world=moving_grid2world,
                )

                mapping.forward = disp_data[..., 0]
                mapping.backward = disp_data[..., 1]
                mapping.is_inverse = True

            # Transforming the image
            if moving_image_full is None:
                transformed = mapping.transform(moving_image)
            else:
                transformed = np.concatenate(
                    [
                        mapping.transform(moving_image)[..., None]
                        for moving_image in moving_image_full
                    ],
                    axis=-1,
                )

            save_nifti(out_file, transformed, affine=static_grid2world)


class SynRegistrationFlow(Workflow):
    def run(
        self,
        static_image_files,
        moving_image_files,
        prealign_file="",
        inv_static=False,
        level_iters=(10, 10, 5),
        metric="cc",
        mopt_sigma_diff=2.0,
        mopt_radius=4,
        mopt_smooth=0.0,
        mopt_inner_iter=0,
        mopt_q_levels=256,
        mopt_double_gradient=True,
        mopt_step_type="",
        step_length=0.25,
        ss_sigma_factor=0.2,
        opt_tol=1e-5,
        inv_iter=20,
        inv_tol=1e-3,
        out_dir="",
        out_warped="warped_moved.nii.gz",
        out_inv_static="inc_static.nii.gz",
        out_field="displacement_field.nii.gz",
    ):
        """
        Parameters
        ----------
        static_image_files : string or Path
            Path of the static image file.
        moving_image_files : string or Path
            Path to the moving image file.
        prealign_file : string or Path, optional
            The text file containing pre alignment information via an affine matrix.
        inv_static : boolean, optional
            Apply the inverse mapping to the static image.
        level_iters : variable int, optional
            The number of iterations at each level of the gaussian pyramid.
        metric : string, optional
            The metric to be used.
            metric available: cc (Cross Correlation), ssd (Sum Squared
            Difference), em (Expectation-Maximization).
        mopt_sigma_diff : float, optional
            Metric option applied on Cross correlation (CC).
            The standard deviation of the Gaussian smoothing kernel to be
            applied to the update field at each iteration.
        mopt_radius : int, optional
            Metric option applied on Cross correlation (CC).
            the radius of the squared (cubic) neighborhood at each voxel to
            be considered to compute the cross correlation.
        mopt_smooth : float, optional
            Metric option applied on Sum Squared Difference (SSD) and
            Expectation Maximization (EM). Smoothness parameter, the
            larger the value the smoother the deformation field.
            (default 1.0 for EM, 4.0 for SSD)
        mopt_inner_iter : int, optional
            Metric option applied on Sum Squared Difference (SSD) and
            Expectation Maximization (EM). This is number of iterations to be
            performed at each level of the multi-resolution Gauss-Seidel
            optimization algorithm (this is not the number of steps per
            Gaussian Pyramid level, that parameter must be set for the
            optimizer, not the metric). Default 5 for EM, 10 for SSD.
        mopt_q_levels : int, optional
            Metric option applied on Expectation Maximization (EM).
            Number of quantization levels (Default: 256 for EM)
        mopt_double_gradient : bool, optional
            Metric option applied on Expectation Maximization (EM).
            if True, the gradient of the expected static image under the moving
            modality will be added to the gradient of the moving image,
            similarly, the gradient of the expected moving image under the
            static modality will be added to the gradient of the static image.
        mopt_step_type : string, optional
            Metric option applied on Sum Squared Difference (SSD) and
            Expectation Maximization (EM). The optimization schedule to be
            used in the multi-resolution Gauss-Seidel optimization algorithm
            (not used if Demons Step is selected). Possible value:
            ('gauss_newton', 'demons'). default: 'gauss_newton' for EM,
            'demons' for SSD.
        step_length : float, optional
            the length of the maximum displacement vector of the update
            displacement field at each iteration.
        ss_sigma_factor : float, optional
            parameter of the scale-space smoothing kernel. For example, the
            std. dev. of the kernel will be factor*(2^i) in the isotropic case
            where i = 0, 1, ..., n_scales is the scale.
        opt_tol : float, optional
            the optimization will stop when the estimated derivative of the
            energy profile w.r.t. time falls below this threshold.
        inv_iter : int, optional
            the number of iterations to be performed by the displacement field
            inversion algorithm.
        inv_tol : float, optional
            the displacement field inversion algorithm will stop iterating
            when the inversion error falls below this threshold.
        out_dir : string or Path, optional
            Directory to save the transformed files.
        out_warped : string, optional
            Name of the warped file.
        out_inv_static : string, optional
            Name of the file to save the static image after applying the
            inverse mapping.
        out_field : string, optional
            Name of the file to save the diffeomorphic map.

        """
        io_it = self.get_io_iterator()
        metric = metric.lower()
        if metric not in ["ssd", "cc", "em"]:
            raise ValueError(
                "Invalid similarity metric: Please"
                " provide a valid metric like 'ssd', 'cc', 'em'"
            )

        logger.info("Starting Diffeomorphic Registration")
        logger.info(f"Using {metric.upper()} Metric")

        # Init parameter if they are not setup
        init_param = {
            "ssd": {
                "mopt_smooth": 4.0,
                "mopt_inner_iter": 10,
                "mopt_step_type": "demons",
            },
            "em": {
                "mopt_smooth": 1.0,
                "mopt_inner_iter": 5,
                "mopt_step_type": "gauss_newton",
            },
        }

        mopt_smooth = (
            mopt_smooth
            if mopt_smooth or metric == "cc"
            else init_param[metric]["mopt_smooth"]
        )
        mopt_inner_iter = (
            mopt_inner_iter
            if mopt_inner_iter or metric == "cc"
            else init_param[metric]["mopt_inner_iter"]
        )

        # If using the 'cc' metric, force the `mopt_step_type` parameter to an
        # empty value since the 'cc' metric does not use it; for the rest of
        # the metrics, the `step_type` parameter will be initialized to their
        # corresponding default values in `init_param`.
        if metric == "cc":
            mopt_step_type = ""

        for (
            static_file,
            moving_file,
            owarped_file,
            oinv_static_file,
            omap_file,
        ) in io_it:
            logger.info(f"Loading static file {static_file}")
            logger.info(f"Loading moving file {moving_file}")

            # Loading the image data from the input files into object.
            static_image, static_grid2world = load_nifti(static_file)
            moving_image, moving_grid2world = load_nifti(moving_file)

            # Sanity check for the input image dimensions.
            check_dimensions(static_image, moving_image)

            # Loading the affine matrix.
            prealign = np.loadtxt(prealign_file) if prealign_file else None

            # Note that `step_type` is initialized to the default value in
            # `init_param` for the metric that was not specified as a
            # parameter or if the `mopt_step_type` is empty.
            l_metric = {
                "ssd": SSDMetric(
                    static_image.ndim,
                    smooth=mopt_smooth,
                    inner_iter=mopt_inner_iter,
                    step_type=mopt_step_type
                    if (mopt_step_type and mopt_step_type.strip()) and metric == "ssd"
                    else init_param["ssd"]["mopt_step_type"],
                ),
                "cc": CCMetric(
                    static_image.ndim, sigma_diff=mopt_sigma_diff, radius=mopt_radius
                ),
                "em": EMMetric(
                    static_image.ndim,
                    smooth=mopt_smooth,
                    inner_iter=mopt_inner_iter,
                    step_type=mopt_step_type
                    if (mopt_step_type and mopt_step_type.strip()) and metric == "em"
                    else init_param["em"]["mopt_step_type"],
                    q_levels=mopt_q_levels,
                    double_gradient=mopt_double_gradient,
                ),
            }

            current_metric = l_metric.get(metric.lower())

            sdr = SymmetricDiffeomorphicRegistration(
                metric=current_metric,
                level_iters=level_iters,
                step_length=step_length,
                ss_sigma_factor=ss_sigma_factor,
                opt_tol=opt_tol,
                inv_iter=inv_iter,
                inv_tol=inv_tol,
            )

            mapping = sdr.optimize(
                static_image,
                moving_image,
                static_grid2world=static_grid2world,
                moving_grid2world=moving_grid2world,
                prealign=prealign,
            )

            mapping_data = np.array([mapping.forward.T, mapping.backward.T]).T
            warped_moving = mapping.transform(moving_image)
            mapping.is_inverse = True
            inv_static = mapping.transform(static_image)

            # Saving
            logger.info(f"Saving warped {owarped_file}")
            save_nifti(owarped_file, warped_moving, static_grid2world)
            logger.info(f"Saving inverse transformes static {oinv_static_file}")
            save_nifti(oinv_static_file, inv_static, static_grid2world)
            logger.info(f"Saving Diffeomorphic map {omap_file}")
            save_nifti(omap_file, mapping_data, mapping.codomain_world2grid)


class MotionCorrectionFlow(Workflow):
    """
    The Motion Correction workflow allows the user to align between-volumes
    DWI dataset.
    """

    def run(
        self,
        input_files,
        bvalues_files,
        bvectors_files,
        b0_threshold=50,
        bvecs_tol=0.01,
        out_dir="",
        out_moved="moved.nii.gz",
        out_affine="affine.txt",
    ):
        """
        Parameters
        ----------
        input_files : string or Path
            Path to the input volumes. This path may contain wildcards to
            process multiple inputs at once.
        bvalues_files : string or Path
            Path to the bvalues files. This path may contain wildcards to use
            multiple bvalues files at once.
        bvectors_files : string or Path
            Path to the bvectors files. This path may contain wildcards to use
            multiple bvectors files at once.
        b0_threshold : float, optional
            Threshold used to find b0 volumes.
        bvecs_tol : float, optional
            Threshold used to check that norm(bvec) = 1 +/- bvecs_tol
            b-vectors are unit vectors
        out_dir : string or Path, optional
            Directory to save the transformed image and the affine matrix.
        out_moved : string, optional
            Name for the saved transformed image.
        out_affine : string, optional
            Name for the saved affine matrix.
        """

        io_it = self.get_io_iterator()

        for dwi, bval, bvec, omoved, oafffine in io_it:
            # Load the data from the input files and store into objects.
            logger.info(f"Loading {dwi}")
            data, affine = load_nifti(dwi)

            bvals, bvecs = read_bvals_bvecs(bval, bvec)
            # If all b-values are smaller or equal to the b0 threshold, it is
            # assumed that no thresholding is requested
            if any(mask_non_weighted_bvals(bvals, b0_threshold)):
                if b0_threshold < bvals.min():
                    warn(
                        f"b0_threshold (value: {b0_threshold}) is too low, "
                        "increase your b0_threshold. It should be higher than the "
                        f"lowest b0 value ({bvals.min()}).",
                        stacklevel=2,
                    )
            gtab = gradient_table(
                bvals, bvecs=bvecs, b0_threshold=b0_threshold, atol=bvecs_tol
            )

            reg_img, reg_affines = motion_correction(
                data=data, gtab=gtab, affine=affine
            )

            # Saving the corrected image file
            save_nifti(omoved, reg_img.get_fdata(), affine)
            # Write the affine matrix array to disk
            with open(oafffine, "w") as outfile:
                outfile.write(f"# Array shape: {reg_affines.shape}\n")
                for affine_slice in reg_affines:
                    np.savetxt(outfile, affine_slice, fmt="%-7.2f")
                    outfile.write("# New slice\n")


class BundleWarpFlow(Workflow):
    @classmethod
    def get_short_name(cls):
        return "bundlewarp"

    def run(
        self,
        static_file,
        moving_file,
        dist=None,
        alpha=0.3,
        beta=20,
        max_iter=15,
        affine=True,
        bbox_valid_check=True,
        out_dir="",
        out_linear_moved="linearly_moved.trx",
        out_nonlinear_moved="nonlinearly_moved.trx",
        out_warp_transform="warp_transform.npy",
        out_warp_kernel="warp_kernel.npy",
        out_dist="distance_matrix.npy",
        out_matched_pairs="matched_pairs.npy",
    ):
        """BundleWarp: streamline-based nonlinear registration.

        BundleWarp :footcite:p:`Chandio2023` is a nonrigid registration method
        for deformable registration of white matter tracts.

        Parameters
        ----------
        static_file : string or Path
            Path to the static (reference) .trx file.
        moving_file : string or Path
            Path to the moving (target to be registered) .trx file.
        dist : string, optional
            Path to the precalculated distance matrix file.
        alpha : float, optional
            Represents the trade-off between regularizing the deformation and
            having points match very closely. Lower value of alpha means high
            deformations. It is represented with λ in BundleWarp paper. NOTE:
            setting alpha<=0.01 will result in highly deformable registration
            that could extremely modify the original anatomy of the moving
            bundle.
        beta : int, optional
            Represents the strength of the interaction between points
            Gaussian kernel size.
        max_iter : int, optional
            Maximum number of iterations for deformation process in ml-CPD
            method.
        affine : boolean, optional
            If False, use rigid registration as starting point.
        bbox_valid_check : boolean, optional
            Verification for negative voxel coordinates or values above the volume
            dimensions.
        out_dir : string or Path, optional
            Output directory.
        out_linear_moved : string, optional
            Filename of linearly moved bundle.
        out_nonlinear_moved : string, optional
            Filename of nonlinearly moved (warped) bundle.
        out_warp_transform : string, optional
            Filename of warp transformations generated by BundleWarp.
        out_warp_kernel : string, optional
            Filename of regularization gaussian kernel generated by BundleWarp.
        out_dist : string, optional
            Filename of MDF distance matrix.
        out_matched_pairs : string, optional
            Filename of matched pairs; streamline correspondences between two
            bundles.

        References
        ----------
        .. footbibliography::
        """

        logger.info(f"Loading static file {static_file}")
        logger.info(f"Loading moving file {moving_file}")

        static_obj = load_tractogram(
            static_file, "same", bbox_valid_check=bbox_valid_check
        )
        moving_obj = load_tractogram(
            moving_file, "same", bbox_valid_check=bbox_valid_check
        )

        static = static_obj.streamlines
        moving = moving_obj.streamlines

        static = set_number_of_points(static, nb_points=20)
        moving = set_number_of_points(moving, nb_points=20)

        deformed_bundle, affine_bundle, _, mp, warp = bundlewarp(
            static,
            moving,
            dist=dist,
            alpha=alpha,
            beta=beta,
            max_iter=max_iter,
            affine=affine,
        )

        logger.info(f"Saving output file {out_linear_moved}")
        new_tractogram = StatefulTractogram(
            affine_bundle,
            moving_obj,
            moving_obj.space,
        )
        save_tractogram(
            new_tractogram,
            str(Path(out_dir) / out_linear_moved),
            bbox_valid_check=bbox_valid_check,
        )

        logger.info(f"Saving output file {out_nonlinear_moved}")
        new_tractogram = StatefulTractogram(
            deformed_bundle, moving_obj, moving_obj.space
        )
        save_tractogram(
            new_tractogram,
            str(Path(out_dir) / out_nonlinear_moved),
            bbox_valid_check=bbox_valid_check,
        )

        logger.info(f"Saving output file {out_warp_transform}")
        np.save(Path(out_dir) / out_warp_transform, np.array(warp["transforms"]))

        logger.info(f"Saving output file {out_warp_kernel}")
        np.save(Path(out_dir) / out_warp_kernel, np.array(warp["gaussian_kernel"]))

        logger.info(f"Saving output file {out_dist}")
        np.save(Path(out_dir) / out_dist, dist)

        logger.info(f"Saving output file {out_matched_pairs}")
        np.save(Path(out_dir) / out_matched_pairs, mp)
