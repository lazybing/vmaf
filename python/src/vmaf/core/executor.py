from abc import ABCMeta, abstractmethod
import multiprocessing
import os
from time import sleep
import hashlib
from vmaf.core.asset import Asset
from vmaf.tools.decorator import deprecated

from vmaf.tools.misc import make_parent_dirs_if_nonexist, get_dir_without_last_slash, \
    parallel_map, match_any_files, run_process, \
    get_file_name_extension, get_normalized_string_from_dict
from vmaf.core.mixin import TypeVersionEnabled
from vmaf.config import VmafExternalConfig

__copyright__ = "Copyright 2016-2018, Netflix, Inc."
__license__ = "Apache, Version 2.0"

class Executor(TypeVersionEnabled):
    """
    An Executor takes in a list of Assets, and run computations on them, and
    return a list of corresponding Results. An Executor must specify a unique
    type and version combination (by the TYPE and VERSION attribute), so that
    the Result generated by it can be uniquely identified.

    Executor is the base class for FeatureExtractor and QualityRunner, and it
    provides a number of shared housekeeping functions, including reusing
    Results, creating FIFO pipes, cleaning up log files/Results, etc.
    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def _generate_result(self, asset):
        raise NotImplementedError

    @abstractmethod
    def _read_result(self, asset):
        raise NotImplementedError

    def __init__(self,
                 assets,
                 logger,
                 fifo_mode=True,
                 delete_workdir=True,
                 result_store=None,
                 optional_dict=None,
                 optional_dict2=None,
                 ):
        """
        Use optional_dict for parameters that would impact result (e.g. model,
        patch size), and use optional_dict2 for parameters that would NOT
        impact result (e.g. path to data cache file).
        """

        TypeVersionEnabled.__init__(self)

        self.assets = assets
        self.logger = logger
        self.fifo_mode = fifo_mode
        self.delete_workdir = delete_workdir
        self.results = []
        self.result_store = result_store
        self.optional_dict = optional_dict
        self.optional_dict2 = optional_dict2

        self._assert_class()
        self._assert_args()
        self._assert_assets()

        self._custom_init()

    def _custom_init(self):
        pass

    @property
    def executor_id(self):
        executor_id_ = TypeVersionEnabled.get_type_version_string(self)

        if self.optional_dict is not None and len(self.optional_dict) > 0:
            # include optional_dict info in executor_id for result store,
            # as parameters in optional_dict will impact result
            executor_id_ += '_{}'.format(get_normalized_string_from_dict(self.optional_dict))
        return executor_id_

    def run(self, **kwargs):
        """
        Do all the computation here.
        :return:
        """
        if self.logger:
            self.logger.info(
                "For each asset, if {type} result has not been generated, run "
                "and generate {type} result...".format(type=self.executor_id))

        if 'parallelize' in kwargs:
            parallelize = kwargs['parallelize']
        else:
            parallelize = False

        if parallelize:
            # create locks for unique assets (uniqueness is identified by str(asset))
            map_asset_lock = {}
            locks = []
            for asset in self.assets:
                asset_str = str(asset)
                if asset_str not in map_asset_lock:
                    map_asset_lock[asset_str] = multiprocessing.Lock()
                locks.append(map_asset_lock[asset_str])

            # pack key arguments to be used as inputs to map function
            list_args = []
            for asset, lock in zip(self.assets, locks):
                list_args.append(
                    [asset, lock])

            def _run(asset_lock):
                asset, lock = asset_lock
                lock.acquire()
                result = self._run_on_asset(asset)
                lock.release()
                return result

            self.results = parallel_map(_run, list_args)
        else:
            self.results = map(self._run_on_asset, self.assets)

    def remove_results(self):
        """
        Remove all relevant Results stored in ResultStore, which is specified
        at the constructor.
        :return:
        """
        for asset in self.assets:
            self._remove_result(asset)

    @classmethod
    def _assert_class(cls):
        pass

    def _assert_args(self):
        pass

    def _assert_assets(self):

        for asset in self.assets:
            self._assert_an_asset(asset)

        pass

    @staticmethod
    def _need_ffmpeg(asset):
        # 1) if quality width/height do not to agree with ref/dis width/height,
        # must rely on ffmpeg for scaling
        # 2) if crop/pad is need, need ffmpeg
        # 3) if ref/dis videos' start/end frames specified, need ffmpeg for
        # frame extraction
        return asset.quality_width_height != asset.ref_width_height \
            or asset.quality_width_height != asset.dis_width_height \
            or asset.crop_cmd is not None \
            or asset.pad_cmd is not None \
            or asset.ref_yuv_type == 'notyuv' \
            or asset.dis_yuv_type == 'notyuv' \
            or asset.ref_start_end_frame is not None \
            or asset.dis_start_end_frame is not None

    @classmethod
    def _assert_an_asset(cls, asset):

        # needed by _generate_result, and by _open_ref_workfile or
        # _open_dis_workfile if called
        assert asset.quality_width_height is not None

        if cls._need_ffmpeg(asset):
            VmafExternalConfig.get_and_assert_ffmpeg()

        # ref_yuv_type and dis_yuv_type must match, unless any of them is notyuv.
        # also check the logic in _get_workfile_yuv_type
        assert (asset.ref_yuv_type == 'notyuv' or asset.dis_yuv_type == 'notyuv') \
               or (asset.ref_yuv_type == asset.dis_yuv_type)

        # if crop_cmd or pad_cmd is specified, make sure quality_width and
        # quality_height are EXPLICITLY specified in asset_dict
        if asset.crop_cmd is not None:
            assert 'quality_width' in asset.asset_dict and 'quality_height' in asset.asset_dict, \
                'If crop_cmd is specified, must also EXPLICITLY specify quality_width and quality_height.'
        if asset.pad_cmd is not None:
            assert 'quality_width' in asset.asset_dict and 'quality_height' in asset.asset_dict, \
                'If pad_cmd is specified, must also EXPLICITLY specify quality_width and quality_height.'

    @staticmethod
    def _get_workfile_yuv_type(asset):
        """ Same as original yuv type, unless it is notyuv; in this case, check
        the other's (if ref, check dis'; vice versa); if both notyuv, use format as set at a higher level"""

        # also check the logic in _assert_an_asset. The assumption is:
        # assert (asset.ref_yuv_type == 'notyuv' or asset.dis_yuv_type == 'notyuv') \
        #        or (asset.ref_yuv_type == asset.dis_yuv_type)

        if asset.ref_yuv_type == 'notyuv' and asset.dis_yuv_type == 'notyuv':
            return asset.workfile_yuv_type
        elif asset.ref_yuv_type == 'notyuv' and asset.dis_yuv_type != 'notyuv':
            return asset.dis_yuv_type
        elif asset.ref_yuv_type != 'notyuv' and asset.dis_yuv_type == 'notyuv':
            return asset.ref_yuv_type
        else: # neither notyuv
            assert asset.ref_yuv_type == asset.dis_yuv_type, "YUV types for ref and dis do not match."
            return asset.ref_yuv_type

    def _wait_for_workfiles(self, asset):
        # wait til workfile paths being generated
        for i in range(10):
            if os.path.exists(asset.ref_workfile_path) and \
                    os.path.exists(asset.dis_workfile_path):
                break
            sleep(0.1)
        else:
            raise RuntimeError("ref or dis video workfile path {ref} or {dis} is missing.".format(ref=asset.ref_workfile_path, dis=asset.dis_workfile_path))

    def _prepare_log_file(self, asset):

        log_file_path = self._get_log_file_path(asset)

        # if parent dir doesn't exist, create
        make_parent_dirs_if_nonexist(log_file_path)

        # add runner type and version
        with open(log_file_path, 'wt') as log_file:
            log_file.write("{type_version_str}\n\n".format(
                type_version_str=self.get_cozy_type_version_string()))

    def _assert_paths(self, asset):
        assert os.path.exists(asset.ref_path) or match_any_files(asset.ref_path), \
            "Reference path {} does not exist.".format(asset.ref_path)
        assert os.path.exists(asset.dis_path) or match_any_files(asset.dis_path), \
            "Distorted path {} does not exist.".format(asset.dis_path)

    def _run_on_asset(self, asset):
        # Wraper around the essential function _generate_result, to
        # do housekeeping work including 1) asserts of asset, 2) skip run if
        # log already exist, 3) creating fifo, 4) delete work file and dir

        if self.result_store:
            result = self.result_store.load(asset, self.executor_id)
        else:
            result = None

        # if result can be retrieved from result_store, skip log file
        # generation and reading result from log file, but directly return
        # return the retrieved result
        if result is not None:
            if self.logger:
                self.logger.info('{id} result exists. Skip {id} run.'.
                                 format(id=self.executor_id))
        else:

            if self.logger:
                self.logger.info('{id} result does\'t exist. Perform {id} '
                                 'calculation.'.format(id=self.executor_id))

            # at this stage, it is certain that asset.ref_path and
            # asset.dis_path will be used. must early determine that
            # they exists
            self._assert_paths(asset)

            # if no rescaling is involved, directly work on ref_path/dis_path,
            # instead of opening workfiles
            self._set_asset_use_path_as_workpath(asset)

            # remove workfiles if exist (do early here to avoid race condition
            # when ref path and dis path have some overlap)
            if asset.use_path_as_workpath:
                # do nothing
                pass
            else:
                self._close_workfiles(asset)

            log_file_path = self._get_log_file_path(asset)
            make_parent_dirs_if_nonexist(log_file_path)

            if asset.use_path_as_workpath:
                # do nothing
                pass
            else:
                if self.fifo_mode:
                    self._open_workfiles_in_fifo_mode(asset)
                else:
                    self.open_workfiles(asset)

            self._prepare_log_file(asset)

            self._generate_result(asset)

            # clean up workfiles
            if self.delete_workdir:
                if asset.use_path_as_workpath:
                    # do nothing
                    pass
                else:
                    self._close_workfiles(asset)

            if self.logger:
                self.logger.info("Read {id} log file, get scores...".
                                 format(id=self.executor_id))

            # collect result from each asset's log file
            result = self._read_result(asset)

            # save result
            if self.result_store:
                result = self._save_result(result)

            # clean up workdir and log files in it
            if self.delete_workdir:

                # remove log file
                self._remove_log(asset)

                # remove dir
                log_file_path = self._get_log_file_path(asset)
                log_dir = get_dir_without_last_slash(log_file_path)
                try:
                    os.rmdir(log_dir)
                except OSError as e:
                    if e.errno == 39: # [Errno 39] Directory not empty
                        # e.g. VQM could generate an error file with non-critical
                        # information like: '3 File is longer than 15 seconds.
                        # Results will be calculated using first 15 seconds
                        # only.' In this case, want to keep this
                        # informational file and pass
                        pass

        result = self._post_process_result(result)

        return result

    def open_workfiles(self, asset):
        self._open_ref_workfile(asset, fifo_mode=False)
        self._open_dis_workfile(asset, fifo_mode=False)

    def _open_workfiles_in_fifo_mode(self, asset):
        ref_p = multiprocessing.Process(target=self._open_ref_workfile,
                                        args=(asset, True))
        dis_p = multiprocessing.Process(target=self._open_dis_workfile,
                                        args=(asset, True))
        ref_p.start()
        dis_p.start()
        self._wait_for_workfiles(asset)

    @classmethod
    def _close_workfiles(cls, asset):
        cls._close_ref_workfile(asset)
        cls._close_dis_workfile(asset)

    def _refresh_workfiles_before_additional_pass(self, asset):
        # If fifo mode and workpath needs to be freshly generated, must
        # reopen the fifo pipe before proceeding
        if self.fifo_mode and (not asset.use_path_as_workpath):
            self._close_workfiles(asset)
            self._open_workfiles_in_fifo_mode(asset)

    def _save_result(self, result):
        self.result_store.save(result)
        return result

    @classmethod
    def _set_asset_use_path_as_workpath(cls, asset):
        # if no rescaling or croping or padding is involved, directly work on
        # ref_path/dis_path, instead of opening workfiles
        if not cls._need_ffmpeg(asset):
            asset.use_path_as_workpath = True

    @classmethod
    def _post_process_result(cls, result):
        # do nothing, wait to be overridden
        return result

    def _get_log_file_path(self, asset):
        return "{workdir}/{executor_id}_{str}".format(
            workdir=asset.workdir, executor_id=self.executor_id,
            str=hashlib.sha1(str(asset).encode("utf-8")).hexdigest())

    # ===== workfile =====

    def _open_ref_workfile(self, asset, fifo_mode):
        # For now, only works for YUV format -- all need is to copy from ref
        # file to ref workfile

        # only need to open ref workfile if the path is different from ref path
        assert asset.use_path_as_workpath is False and asset.ref_path != asset.ref_workfile_path

        # if fifo mode, mkfifo
        if fifo_mode:
            os.mkfifo(asset.ref_workfile_path)

        quality_width, quality_height = self._get_quality_width_height(asset)
        yuv_type = asset.ref_yuv_type
        resampling_type = self._get_resampling_type(asset)

        if yuv_type != 'notyuv':
            # in this case, for sure has ref_width_height
            width, height = asset.ref_width_height
            src_fmt_cmd = self._get_yuv_src_fmt_cmd(asset, height, width, 'ref')
        else:
            src_fmt_cmd = self._get_notyuv_src_fmt_cmd(asset, 'ref')

        workfile_yuv_type = self._get_workfile_yuv_type(asset)

        crop_cmd = self._get_crop_cmd(asset)
        pad_cmd = self._get_pad_cmd(asset)

        vframes_cmd, select_cmd = self._get_vframes_cmd(asset, 'ref')

        ffmpeg_cmd = '{ffmpeg} {src_fmt_cmd} -i {src} -an -vsync 0 ' \
                     '-pix_fmt {yuv_type} {vframes_cmd} -vf {select_cmd}{crop_cmd}{pad_cmd}scale={width}x{height} -f rawvideo ' \
                     '-sws_flags {resampling_type} -y {dst}'
        ffmpeg_cmd = ffmpeg_cmd.format(
            ffmpeg=VmafExternalConfig.get_and_assert_ffmpeg(),
            src=asset.ref_path,
            dst=asset.ref_workfile_path,
            width=quality_width,
            height=quality_height,
            src_fmt_cmd=src_fmt_cmd,
            crop_cmd=crop_cmd,
            pad_cmd=pad_cmd,
            yuv_type=workfile_yuv_type,
            resampling_type=resampling_type,
            vframes_cmd=vframes_cmd,
            select_cmd=select_cmd,
        )

        if self.logger:
            self.logger.info(ffmpeg_cmd)

        run_process(ffmpeg_cmd, shell=True)

    def _open_dis_workfile(self, asset, fifo_mode):
        # For now, only works for YUV format -- all need is to copy from dis
        # file to dis workfile

        # only need to open dis workfile if the path is different from dis path
        assert asset.use_path_as_workpath is False and asset.dis_path != asset.dis_workfile_path

        # if fifo mode, mkfifo
        if fifo_mode:
            os.mkfifo(asset.dis_workfile_path)

        quality_width, quality_height = self._get_quality_width_height(asset)
        yuv_type = asset.dis_yuv_type
        resampling_type = self._get_resampling_type(asset)

        if yuv_type != 'notyuv':
            # in this case, for sure has dis_width_height
            width, height = asset.dis_width_height
            src_fmt_cmd = self._get_yuv_src_fmt_cmd(asset, height, width, 'dis')
        else:
            src_fmt_cmd = self._get_notyuv_src_fmt_cmd(asset, 'dis')

        workfile_yuv_type = self._get_workfile_yuv_type(asset)

        crop_cmd = self._get_crop_cmd(asset)
        pad_cmd = self._get_pad_cmd(asset)

        vframes_cmd, select_cmd = self._get_vframes_cmd(asset, 'dis')

        ffmpeg_cmd = '{ffmpeg} {src_fmt_cmd} -i {src} -an -vsync 0 ' \
                     '-pix_fmt {yuv_type} {vframes_cmd} -vf {select_cmd}{crop_cmd}{pad_cmd}scale={width}x{height} -f rawvideo ' \
                     '-sws_flags {resampling_type} -y {dst}'.format(
            ffmpeg=VmafExternalConfig.get_and_assert_ffmpeg(),
            src=asset.dis_path, dst=asset.dis_workfile_path,
            width=quality_width, height=quality_height,
            src_fmt_cmd=src_fmt_cmd,
            crop_cmd=crop_cmd,
            pad_cmd=pad_cmd,
            yuv_type=workfile_yuv_type,
            resampling_type=resampling_type,
            vframes_cmd=vframes_cmd,
            select_cmd=select_cmd,
        )
        if self.logger:
            self.logger.info(ffmpeg_cmd)

        run_process(ffmpeg_cmd, shell=True)

    def _get_resampling_type(self, asset):
        return asset.resampling_type

    def _get_quality_width_height(self, asset):
        return asset.quality_width_height

    @staticmethod
    def _get_yuv_src_fmt_cmd(asset, height, width, ref_or_dis):
        if ref_or_dis == 'ref':
            yuv_type = asset.ref_yuv_type
        elif ref_or_dis == 'dis':
            yuv_type = asset.dis_yuv_type
        else:
            raise AssertionError('Unknown ref_or_dis: {}'.format(ref_or_dis))
        yuv_src_fmt_cmd = '-f rawvideo -pix_fmt {yuv_fmt} -s {width}x{height}'. \
            format(yuv_fmt=yuv_type, width=width, height=height)
        return yuv_src_fmt_cmd

    @staticmethod
    def _get_notyuv_src_fmt_cmd(asset, ref_or_dis):
        if ref_or_dis == 'ref':
            path = asset.ref_path
        elif ref_or_dis == 'dis':
            path = asset.dis_path
        else:
            assert False, 'ref_or_dis cannot be {}'.format(ref_or_dis)

        if 'icpf' == get_file_name_extension(path) or 'j2c' == get_file_name_extension(path):
            # 2147483647 is INT_MAX if int is 4 bytes
            return "-start_number_range 2147483647"
        else:
            return ""

    def _get_crop_cmd(self, asset):
        crop_cmd = "crop={},".format(
            asset.crop_cmd) if asset.crop_cmd is not None else ""
        return crop_cmd

    def _get_pad_cmd(self, asset):
        pad_cmd = "pad={},".format(
            asset.pad_cmd) if asset.pad_cmd is not None else ""
        return pad_cmd

    def _get_vframes_cmd(self, asset, ref_or_dis):
        if ref_or_dis == 'ref':
            start_end_frame = asset.ref_start_end_frame
        elif ref_or_dis == 'dis':
            start_end_frame = asset.dis_start_end_frame
        else:
            raise AssertionError('Unknown ref_or_dis: {}'.format(ref_or_dis))

        if start_end_frame is None:
            return "", ""
        else:
            start_frame, end_frame = start_end_frame
            num_frames = end_frame - start_frame + 1
            return "-vframes {}".format(num_frames), \
                   "select='gte(n\,{start_frame})*gte({end_frame}\,n)',setpts=PTS-STARTPTS,".format(
                       start_frame=start_frame, end_frame=end_frame)

    @staticmethod
    def _close_ref_workfile(asset):

        # only need to close ref workfile if the path is different from ref path
        assert asset.use_path_as_workpath is False and asset.ref_path != asset.ref_workfile_path

        # caution: never remove ref file!!!!!!!!!!!!!!!
        if os.path.exists(asset.ref_workfile_path):
            os.remove(asset.ref_workfile_path)

    @staticmethod
    def _close_dis_workfile(asset):

        # only need to close dis workfile if the path is different from dis path
        assert asset.use_path_as_workpath is False and asset.dis_path != asset.dis_workfile_path

        # caution: never remove dis file!!!!!!!!!!!!!!
        if os.path.exists(asset.dis_workfile_path):
            os.remove(asset.dis_workfile_path)

    def _remove_log(self, asset):
        log_file_path = self._get_log_file_path(asset)
        if os.path.exists(log_file_path):
            os.remove(log_file_path)

    def _remove_result(self, asset):
        if self.result_store:
            self.result_store.delete(asset, self.executor_id)

@deprecated
def run_executors_in_parallel(executor_class,
                              assets,
                              fifo_mode=True,
                              delete_workdir=True,
                              parallelize=True,
                              logger=None,
                              result_store=None,
                              optional_dict=None,
                              optional_dict2=None,
                              ):
    """
    Run multiple Executors in parallel.
    """

    # construct an executor object just to call _assert_assets() only
    executor_class(
        assets,
        logger,
        fifo_mode=fifo_mode,
        delete_workdir=True,
        result_store=result_store,
        optional_dict=optional_dict,
        optional_dict2=optional_dict2
    )

    # create locks for unique assets (uniqueness is identified by str(asset))
    map_asset_lock = {}
    locks = []
    for asset in assets:
        asset_str = str(asset)
        if asset_str not in map_asset_lock:
            map_asset_lock[asset_str] = multiprocessing.Lock()
        locks.append(map_asset_lock[asset_str])

    # pack key arguments to be used as inputs to map function
    list_args = []
    for asset, lock in zip(assets, locks):
        list_args.append(
            [executor_class, asset, fifo_mode, delete_workdir,
             result_store, optional_dict, optional_dict2, lock])

    def run_executor(args):
        executor_class, asset, fifo_mode, delete_workdir, \
        result_store, optional_dict, optional_dict2, lock = args
        lock.acquire()
        executor = executor_class([asset], None, fifo_mode, delete_workdir,
                                  result_store, optional_dict, optional_dict2)
        executor.run()
        lock.release()
        return executor

    # run
    if parallelize:
        executors = parallel_map(run_executor, list_args, processes=None)
    else:
        executors = map(run_executor, list_args)

    # aggregate results
    results = [executor.results[0] for executor in executors]

    return executors, results


class NorefExecutorMixin(object):
    """ Override Executor whenever reference video is mentioned. """

    @staticmethod
    def _need_ffmpeg(asset):
        # Override Executor._need_ffmpeg.
        # 1) if quality width/height do not to agree with dis width/height,
        # must rely on ffmpeg for scaling
        # 2) if crop/pad is need, need ffmpeg
        # 3) if dis videos' start/end frames specified, need ffmpeg for
        # frame extraction
        return asset.quality_width_height != asset.dis_width_height \
            or asset.crop_cmd is not None \
            or asset.pad_cmd is not None \
            or asset.dis_yuv_type == 'notyuv' \
            or asset.dis_start_end_frame is not None

    @classmethod
    def _assert_an_asset(cls, asset):

        # needed by _generate_result, and by _open_dis_workfile if called
        assert asset.quality_width_height is not None

        if cls._need_ffmpeg(asset):
            VmafExternalConfig.get_and_assert_ffmpeg()

        # if crop_cmd or pad_cmd is specified, make sure quality_width and
        # quality_height are EXPLICITLY specified in asset_dict
        if asset.crop_cmd is not None:
            assert 'quality_width' in asset.asset_dict and 'quality_height' in asset.asset_dict, \
                'If crop_cmd is specified, must also EXPLICITLY specify quality_width and quality_height.'
        if asset.pad_cmd is not None:
            assert 'quality_width' in asset.asset_dict and 'quality_height' in asset.asset_dict, \
                'If pad_cmd is specified, must also EXPLICITLY specify quality_width and quality_height.'

    @staticmethod
    def _get_workfile_yuv_type(asset):
        """ Same as original yuv type, unless it is notyuv; in this case,
        use format as set at a higher level"""

        if asset.dis_yuv_type == 'notyuv':
            return asset.workfile_yuv_type
        else:
            return asset.dis_yuv_type

    def _wait_for_workfiles(self, asset):
        # Override Executor._wait_for_workfiles to skip ref_workfile_path
        # wait til workfile paths being generated
        for i in range(10):
            if os.path.exists(asset.dis_workfile_path):
                break
            sleep(0.1)
        else:
            raise RuntimeError("dis video workfile path {} is missing.".format(
                asset.dis_workfile_path))

    def _assert_paths(self, asset):
        # Override Executor._assert_paths to skip asserting on ref_path
        assert os.path.exists(asset.dis_path) or match_any_files(asset.dis_path), \
            "Distorted path {} does not exist.".format(asset.dis_path)

    def open_workfiles(self, asset):
        self._open_dis_workfile(asset, fifo_mode=False)

    def _open_workfiles_in_fifo_mode(self, asset):
        dis_p = multiprocessing.Process(target=self._open_dis_workfile,
                                        args=(asset, True))
        dis_p.start()
        self._wait_for_workfiles(asset)

    @classmethod
    def _close_workfiles(cls, asset):
        # Override Executor._close_workfiles to skip ref.
        cls._close_dis_workfile(asset)

