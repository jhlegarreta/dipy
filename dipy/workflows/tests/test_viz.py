from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import numpy.testing as npt
import pytest

from dipy.io.image import save_nifti
from dipy.io.stateful_tractogram import Space, StatefulTractogram
from dipy.io.streamline import save_tractogram
from dipy.io.utils import create_nifti_header
from dipy.testing.decorators import set_random_number_generator, use_xvfb
from dipy.tracking.streamline import Streamlines
from dipy.utils.optpkg import optional_package

fury, has_fury, setup_module = optional_package("fury", min_version="0.10.0")

if has_fury:
    from dipy.viz.horizon.app import horizon
    from dipy.workflows.viz import HorizonFlow


skip_it = use_xvfb == "skip"


@pytest.mark.skipif(skip_it or not has_fury, reason="Requires FURY")
@set_random_number_generator()
def test_horizon_flow(rng):
    s1 = 10 * np.array(
        [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0], [4, 0, 0]], dtype="f8"
    )

    s2 = 10 * np.array(
        [[0, 0, 0], [0, 1, 0], [0, 2, 0], [0, 3, 0], [0, 4, 0]], dtype="f8"
    )

    s3 = 10 * np.array(
        [[0, 0, 0], [1, 0.2, 0], [2, 0.2, 0], [3, 0.2, 0], [4, 0.2, 0]], dtype="f8"
    )

    affine = np.array(
        [
            [1.0, 0.0, 0.0, -98.0],
            [0.0, 1.0, 0.0, -134.0],
            [0.0, 0.0, 1.0, -72.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )

    data = 255 * rng.random((197, 233, 189))
    vox_size = (1.0, 1.0, 1.0)

    streamlines = Streamlines()
    streamlines.append(s1)
    streamlines.append(s2)
    streamlines.append(s3)

    header = create_nifti_header(affine, data.shape, vox_size)
    sft = StatefulTractogram(streamlines, header, Space.RASMM)

    tractograms = [sft]
    images = None

    with TemporaryDirectory() as out_dir:
        horizon(
            tractograms=tractograms,
            images=images,
            cluster=True,
            cluster_thr=5,
            random_colors=False,
            length_lt=np.inf,
            length_gt=0,
            clusters_lt=np.inf,
            clusters_gt=0,
            world_coords=True,
            interactive=False,
            out_png=str(Path(out_dir) / "horizon-flow.png"),
        )

        buan_colors = np.ones(streamlines.get_data().shape)

        horizon(
            tractograms=tractograms,
            buan=True,
            buan_colors=buan_colors,
            world_coords=True,
            interactive=False,
            out_png=str(Path(out_dir) / "buan.png"),
        )

        data = 255 * rng.random((197, 233, 189))

        images = [(data, affine, "test/test.nii.gz")]

        horizon(
            tractograms=tractograms,
            images=images,
            cluster=True,
            cluster_thr=5,
            random_colors=False,
            length_lt=np.inf,
            length_gt=0,
            clusters_lt=np.inf,
            clusters_gt=0,
            world_coords=True,
            interactive=False,
            out_png=str(Path(out_dir) / "horizon-flow-nii-images.png"),
        )

        fimg = Path(out_dir) / "test.nii.gz"
        ftrk = Path(out_dir) / "test.trk"
        fnpy = Path(out_dir) / "test.npy"

        save_nifti(fimg, data, affine)
        dimensions = data.shape
        nii_header = create_nifti_header(affine, dimensions, vox_size)
        sft = StatefulTractogram(streamlines, nii_header, space=Space.RASMM)
        save_tractogram(sft, ftrk, bbox_valid_check=False)

        pvalues = rng.uniform(low=0, high=1, size=(10,))
        np.save(fnpy, pvalues)

        input_files = [ftrk, fimg]

        npt.assert_equal(len(input_files), 2)

        hz_flow = HorizonFlow()

        hz_flow.run(
            input_files=input_files,
            stealth=True,
            out_dir=out_dir,
            out_stealth_png="tmp_x.png",
        )

        npt.assert_equal(Path(Path(out_dir) / "tmp_x.png").exists(), True)
        npt.assert_raises(
            ValueError, hz_flow.run, input_files=input_files, bg_color=(0.2, 0.2)
        )

        hz_flow.run(
            input_files=input_files,
            stealth=True,
            bg_color=[
                0.5,
            ],
            out_dir=out_dir,
            out_stealth_png="tmp_x.png",
        )
        npt.assert_equal(Path(Path(out_dir) / "tmp_x.png").exists(), True)

        input_files = [ftrk, fnpy]

        npt.assert_equal(len(input_files), 2)

        hz_flow.run(
            input_files=input_files,
            stealth=True,
            bg_color=[
                0.5,
            ],
            buan=True,
            buan_thr=0.5,
            buan_highlight=(1, 1, 0),
            out_dir=out_dir,
            out_stealth_png="tmp_x.png",
        )
        npt.assert_equal(Path(Path(out_dir) / "tmp_x.png").exists(), True)

        npt.assert_raises(
            ValueError, hz_flow.run, input_files=input_files, roi_colors=(0.2, 0.2)
        )

        hz_flow.run(
            input_files=input_files,
            stealth=True,
            roi_colors=[
                0.5,
            ],
            out_dir=out_dir,
            out_stealth_png="tmp_x.png",
        )
        npt.assert_equal(Path(Path(out_dir) / "tmp_x.png").exists(), True)
