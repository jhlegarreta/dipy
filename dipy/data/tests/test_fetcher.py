from http.server import HTTPServer, SimpleHTTPRequestHandler
import importlib
import os
from pathlib import Path
import tempfile
from threading import Thread
from urllib.request import pathname2url

import numpy.testing as npt

from dipy.data import SPHERE_FILES
import dipy.data.fetcher as fetcher


def test_check_md5():
    fd, fname = tempfile.mkstemp()
    stored_md5 = fetcher._get_file_md5(fname)
    # If all is well, this shouldn't return anything:
    npt.assert_equal(fetcher.check_md5(fname, stored_md5=stored_md5), None)
    # If None is provided as input, it should silently not check either:
    npt.assert_equal(fetcher.check_md5(fname, stored_md5=None), None)
    # Otherwise, it will raise its exception class:
    npt.assert_raises(fetcher.FetcherError, fetcher.check_md5, fname, stored_md5="foo")


def test_make_fetcher():
    symmetric362 = SPHERE_FILES["symmetric362"]
    symmetric642 = SPHERE_FILES["symmetric642"]
    with tempfile.TemporaryDirectory() as tmpdir:
        stored_md5 = fetcher._get_file_md5(symmetric362)
        stored_md5_642 = fetcher._get_file_md5(symmetric642)

        # create local HTTP Server
        testfile_folder = str(Path(symmetric362).parent) + os.sep
        testfile_url = f"file:{pathname2url(testfile_folder)}"
        print(testfile_url)
        print(symmetric362)
        current_dir = os.getcwd()
        # change pwd to directory containing testfile.
        os.chdir(testfile_folder)
        server = HTTPServer(("localhost", 8000), SimpleHTTPRequestHandler)
        server_thread = Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        # test make_fetcher
        sphere_fetcher = fetcher._make_fetcher(
            "sphere_fetcher",
            tmpdir,
            testfile_url,
            [os.sep + Path(symmetric362).name, os.sep + Path(symmetric642).name],
            ["sphere_name", "sphere_name2"],
            md5_list=[stored_md5, stored_md5_642],
            optional_fnames=["sphere_name2"],
        )

        try:
            sphere_fetcher()
        except Exception as e:
            print(e)
            # stop local HTTP Server
            server.shutdown()

        assert Path(Path(tmpdir) / "sphere_name").is_file()
        assert not Path(Path(tmpdir) / "sphere_name2").is_file()
        npt.assert_equal(
            fetcher._get_file_md5(Path(tmpdir) / "sphere_name"), stored_md5
        )
        try:
            sphere_fetcher(include_optional=True)
        except Exception as e:
            print(e)
            # stop local HTTP Server
            server.shutdown()

        assert Path(Path(tmpdir) / "sphere_name").is_file()
        assert Path(Path(tmpdir) / "sphere_name2").is_file()
        npt.assert_equal(
            fetcher._get_file_md5(Path(tmpdir) / "sphere_name"), stored_md5
        )
        npt.assert_equal(
            fetcher._get_file_md5(Path(tmpdir) / "sphere_name2"), stored_md5_642
        )

        # stop local HTTP Server
        server.shutdown()
        # change to original working directory
        os.chdir(current_dir)


def test_fetch_data():
    symmetric362 = SPHERE_FILES["symmetric362"]
    with tempfile.TemporaryDirectory() as tmpdir:
        md5 = fetcher._get_file_md5(symmetric362)
        bad_md5 = "8" * len(md5)

        newfile = Path(tmpdir) / "testfile.txt"
        # Test that the fetcher can get a file
        testfile_url = symmetric362
        print(testfile_url)
        p = Path(testfile_url)
        testfile_dir, testfile_name = str(p.parent), p.name
        # create local HTTP Server
        test_server_url = f"http://127.0.0.1:8001/{testfile_name}"
        current_dir = os.getcwd()
        # change pwd to directory containing testfile.
        os.chdir(testfile_dir + os.sep)
        # use different port as shutdown() takes time to release socket.
        server = HTTPServer(("localhost", 8001), SimpleHTTPRequestHandler)
        server_thread = Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        files = {"testfile.txt": (test_server_url, md5)}
        try:
            fetcher.fetch_data(files, tmpdir)
        except Exception as e:
            print(e)
            # stop local HTTP Server
            server.shutdown()
        npt.assert_(newfile.exists())

        # Test that the file is replaced when the md5 doesn't match
        with open(newfile, "a") as f:
            f.write("some junk")
        try:
            fetcher.fetch_data(files, tmpdir)
        except Exception as e:
            print(e)
            # stop local HTTP Server
            server.shutdown()
        npt.assert_(newfile.exists())
        npt.assert_equal(fetcher._get_file_md5(newfile), md5)

        # Test that an error is raised when the md5 checksum of the download
        # file does not match the expected value
        files = {"testfile.txt": (test_server_url, bad_md5)}
        npt.assert_raises(fetcher.FetcherError, fetcher.fetch_data, files, tmpdir)

        # stop local HTTP Server
        server.shutdown()
        # change to original working directory
        os.chdir(current_dir)

    def test_dipy_home():
        test_path = "TEST_PATH"
        if "DIPY_HOME" in os.environ:
            old_home = os.environ["DIPY_HOME"]
            del os.environ["DIPY_HOME"]
        else:
            old_home = None

        importlib.reload(fetcher)

        npt.assert_string_equal(
            fetcher.dipy_home, str(Path("~").expanduser() / ".dipy")
        )
        os.environ["DIPY_HOME"] = test_path
        importlib.reload(fetcher)
        npt.assert_string_equal(fetcher.dipy_home, test_path)

        # return to previous state
        if old_home:
            os.environ["DIPY_HOME"] = old_home
