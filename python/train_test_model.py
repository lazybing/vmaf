__copyright__ = "Copyright 2016, Netflix, Inc."
__license__ = "Apache, Version 2.0"

import os
import numpy as np
from tools import indices
import sys
import config
from mixin import TypeVersionEnabled

class TrainTestModel(TypeVersionEnabled):

    def __init__(self, param_dict, logger=None):
        """
        :param param_dict: contains input parameters
        :param logger:
        :return:
        """
        self.param_dict = param_dict
        self.logger = logger

        self.model_dict = {}

    def _assert_trained(self):

        assert 'feature_names' in self.model_dict

        assert 'model' in self.model_dict

        assert 'norm_type' in self.model_dict
        norm_type = self.model_dict['norm_type']
        assert (   norm_type == 'none'
                or norm_type == 'normalize'
                or norm_type == 'clip_0to1'
                or norm_type == 'clip_minus1to1')

        if norm_type == 'normal':
            assert 'mu' in self.model_dict
            assert 'sd' in self.model_dict

        if norm_type == 'clipped':
            assert 'fmin' in self.model_dict
            assert 'fmax' in self.model_dict

    @property
    def feature_names(self):
        self._assert_trained()
        return self.model_dict['feature_names']
    @feature_names.setter
    def feature_names(self, value):
        self.model_dict['feature_names'] = value

    @property
    def norm_type(self):
        return self.model_dict['norm_type']
    @norm_type.setter
    def norm_type(self, value):
        self.model_dict['norm_type'] = value

    @property
    def mus(self):
        return self.model_dict['mus']
    @mus.setter
    def mus(self, value):
        self.model_dict['mus'] = value

    @property
    def sds(self):
        return self.model_dict['sds']
    @sds.setter
    def sds(self, value):
        self.model_dict['sds'] = value

    @property
    def fmins(self):
        return self.model_dict['fmins']
    @fmins.setter
    def fmins(self, value):
        self.model_dict['fmins'] = value

    @property
    def fmaxs(self):
        return self.model_dict['fmaxs']
    @fmaxs.setter
    def fmaxs(self, value):
        self.model_dict['fmaxs'] = value

    @property
    def model(self):
        return self.model_dict['model']
    @model.setter
    def model(self, value):
        self.model_dict['model'] = value

    def to_file(self, filename):

        self._assert_trained()
        info_to_save = {'param_dict': self.param_dict,
                        'model_dict': self.model_dict}
        import joblib
        joblib.dump(info_to_save, filename, compress=9)

    @classmethod
    def from_file(cls, filename, logger):

        train_test_model = cls(param_dict={}, logger=logger)

        import joblib
        info_loaded = joblib.load(filename)
        train_test_model.param_dict = info_loaded['param_dict']
        train_test_model.model_dict = info_loaded['model_dict']

        return train_test_model

    @staticmethod
    def delete(filename):
        if os.path.exists(filename):
            os.remove(filename)

    @staticmethod
    def _predict(model, xs_2d):
        ys_label_pred = model.predict(xs_2d)
        return ys_label_pred

    def predict(self, xs):

        self._assert_trained()

        for name in self.feature_names:
            assert name in xs

        xs_2d = []
        for name in self.feature_names:
            if xs_2d == []:
                xs_2d = np.matrix(xs[name]).T
            else:
                xs_2d = np.hstack((xs_2d, np.matrix(xs[name]).T))
        xs_2d = np.array(xs_2d)

        # normalize xs
        xs_2d = self.normalize_xs(xs_2d)

        # predict
        ys_label_pred = self._predict(self.model, xs_2d)

        # denormalize ys
        ys_label_pred = self.denormalize_ys(ys_label_pred)

        return ys_label_pred

    @staticmethod
    def get_stats(ys_label, ys_label_pred):
        import scipy.stats
        # MSE
        mse = np.mean(np.power(np.array(ys_label) - np.array(ys_label_pred), 2.0))
        # spearman
        srcc, _ = scipy.stats.spearmanr(ys_label, ys_label_pred)
        # pearson
        pcc, _ = scipy.stats.pearsonr(ys_label, ys_label_pred)
        # kendall
        kendall, _ = scipy.stats.kendalltau(ys_label, ys_label_pred)
        result = {'MSE': mse,
                  'SRCC': srcc,
                  'PCC': pcc,
                  'KENDALL': kendall,
                  'ys_label': list(ys_label),
                  'ys_label_pred': list(ys_label_pred)}
        return result

    @classmethod
    def aggregate_stats_list(cls, results):
        aggregate_ys_label = []
        aggregate_ys_label_pred = []
        for result in results:
            aggregate_ys_label += result['ys_label']
            aggregate_ys_label_pred += result['ys_label_pred']
        return cls.get_stats(aggregate_ys_label, aggregate_ys_label_pred)

    @staticmethod
    def get_objective_score(result, type='SRCC'):
        """
        Objective score is something to MAXIMIZE. e.g. SRCC, or -MSE.
        :param result:
        :param type:
        :return:
        """
        if type == 'SRCC':
            return result['SRCC']
        elif type == 'PCC':
            return result['PCC']
        elif type == 'KENDALL':
            return result['KENDALL']
        elif type == 'MSE':
            return -result['MSE']
        else:
            assert False, 'Unknow type: {} for get_score().'.format(type)

    @staticmethod
    def plot_scatter(ax, stats, content_ids=None):
        assert len(stats['ys_label']) == len(stats['ys_label_pred'])

        if content_ids is None:
            ax.scatter(stats['ys_label'], stats['ys_label_pred'])
        else:
            assert len(stats['ys_label']) == len(content_ids)

            unique_content_ids = list(set(content_ids))
            import matplotlib.pyplot as plt
            cmap = plt.get_cmap()
            colors = [cmap(i) for i in np.linspace(0, 1, len(unique_content_ids))]
            for idx, curr_content_id in enumerate(unique_content_ids):
                curr_idxs = indices(content_ids, lambda cid: cid==curr_content_id)
                curr_ys_label = np.array(stats['ys_label'])[curr_idxs]
                curr_ys_label_pred = np.array(stats['ys_label_pred'])[curr_idxs]
                ax.scatter(curr_ys_label, curr_ys_label_pred,
                           label=curr_content_id, color=colors[idx % len(colors)])

    def evaluate(self, xs, ys):
        ys_label_pred = self.predict(xs)
        ys_label = ys['label']
        return self.get_stats(ys_label, ys_label_pred)

    def train(self, xys):

        assert 'label' in xys

        ys_vec = xys['label']

        # this makes sure the order of features are normalized, and each
        # dimension of xys_2d is consistent with feature_names
        feature_names = sorted(xys.keys())

        feature_names.remove('label')
        feature_names.remove('content_id')

        self.feature_names = feature_names

        xs_2d = []
        for name in feature_names:
            if xs_2d == []:
                xs_2d = np.matrix(xys[name]).T
            else:
                xs_2d = np.hstack((xs_2d, np.matrix(xys[name]).T))

        # combine them
        xys_2d = np.array(np.hstack((np.matrix(ys_vec).T, xs_2d)))

        self.norm_type = self.param_dict['norm_type'] if 'norm_type' in self.param_dict else 'normalize'

        # calculate normalization parameters,
        self._calculate_normalization_params(xys_2d)

        # normalize
        xys_2d = self._normalize_xys(xys_2d)

        model = self._train(self.param_dict, xys_2d)

        self.model = model

    def _calculate_normalization_params(self, xys_2d):
        if self.norm_type == 'normalize':
            self.mus = np.mean(xys_2d, axis=0)
            self.sds = np.std(xys_2d, axis=0)
        elif self.norm_type == 'clip_0to1':
            self.fmins = np.min(xys_2d, axis=0)
            self.fmaxs = np.max(xys_2d, axis=0)
        elif self.norm_type == 'clip_minus1to1':
            self.fmins = np.min(xys_2d, axis=0)
            self.fmaxs = np.max(xys_2d, axis=0)
        elif self.norm_type == 'none':
            pass
        else:
            assert False, 'Incorrect feature normalization type selected: {}'. \
                format(self.norm_type)

    def _normalize_xys(self, xys_2d):
        if self.norm_type == 'normalize':
            xys_2d -= self.mus
            xys_2d /= self.sds
        elif self.norm_type == 'clip_0to1':
            xys_2d = 1.0 / (self.fmaxs - self.fmins) * (xys_2d - self.fmins)
        elif self.norm_type == 'clip_minus1to1':
            xys_2d = 2.0 / (self.fmaxs - self.fmins) * (xys_2d - self.fmins) - 1
        elif self.norm_type == 'none':
            pass
        else:
            assert False, 'Incorrect feature normalization type selected: {}' \
                .format(self.norm_type)
        return xys_2d

    def denormalize_ys(self, ys_vec):
        if self.norm_type == 'normalize':
            ys_vec *= self.sds[0]
            ys_vec += self.mus[0]
        # elif self.norm_type == 'clip_0to1':
        # for backward compatibility, use the following for older model files:
        elif self.norm_type == 'clip_0to1' or self.norm_type == 'rescale1':
            ys_vec *= (self.fmaxs[0] - self.fmins[0])
            ys_vec += self.fmins[0]
        # elif self.norm_type == 'clip_minus1to1':
        # for backward compatibility, use the following for older model files:
        elif self.norm_type == 'clip_minus1to1' or self.norm_type == 'rescale2':
            ys_vec += 1
            ys_vec /= 2.0
            ys_vec *= (self.fmaxs[0] - self.fmins[0])
            ys_vec += self.fmins[0]
        elif self.norm_type == 'none':
            pass
        else:
            assert False, 'Incorrect feature normalization type selected: {}'. \
                format(self.norm_type)
        return ys_vec

    def normalize_xs(self, xs_2d):
        if self.norm_type == 'normalize':
            xs_2d -= self.mus[1:]
            xs_2d /= self.sds[1:]
        # elif self.norm_type == 'clip_0to1':
        # for backward compatibility, use the following for older model files:
        elif self.norm_type == 'clip_0to1' or self.norm_type == 'rescale1':
            xs_2d = 1.0 / (self.fmaxs[1:] - self.fmins[1:]) * \
                    (xs_2d - self.fmins[1:])
        # elif self.norm_type == 'clip_minus1to1':
        # for backward compatibility, use the following for older model files:
        elif self.norm_type == 'clip_minus1to1' or self.norm_type == 'rescale2':
            xs_2d = 2.0 / (self.fmaxs[1:] - self.fmins[1:]) * \
                    (xs_2d - self.fmins[1:]) - 1
        elif self.norm_type == 'none':
            pass
        else:
            assert False, 'Incorrect feature normalization type selected: {}' \
                .format(self.norm_type)
        return xs_2d

    @staticmethod
    def get_xs_from_dataframe(df, rows=None):
        """Prepare xs (i.e. a dictionary of named features, e.g.
        xs = {'vif_feat': [0.8, 0.9, 0.5], 'ssim_feat': [1.0, 0.5, 0.6]}),
        which is to be used as input by predict(xs), from a pandas DataFrame
        df, e.g.
             ansnr_feat  content_id  distortion_id  ssim_feat     label
        0     0.8           0              0        1.0           8.4
        1     0.9           1              0        0.5           6.5
        0     0.5           0              0        0.6           4.3
        :param df:
        :param rows: if None, take all rows from df, otherwise must be a list of
        row indices
        :return:
        """
        # by the rule of Extraction, features always end with '_feat'
        feature_names = [name for name in df.columns.values if "_feat" in name]
        xs = {}
        for name in feature_names:
            if rows is None:
                xs[name] = np.array(df[name])
            else:
                xs[name] = np.array(df[name].iloc[rows])
        return xs

    @staticmethod
    def get_ys_from_dataframe(df, rows=None):
        """Prepare ys (i.e. a dictionary with key 'label' and labels, e.g.
        ys = {'label': [8.4, 6.5, 4.3]}), from a pandas DataFrame df, e.g.
             ansnr_feat  content_id  distortion_id  ssim_feat     label
        0     0.8           0              0        1.0           8.4
        1     0.9           1              0        0.5           6.5
        0     0.5           0              0        0.6           4.3
        :param df:
        :param rows: if None, take all rows from df, otherwise must be a list of
        row indices
        :return:
        """
        # by the rule of Extraction, labels must have key 'label'
        ys = {}
        if rows is None:
            ys['label'] = np.array(df['label'])
            ys['content_id'] = np.array(df['content_id'])
        else:
            ys['label'] = np.array(df['label'].iloc[rows])
            ys['content_id'] = np.array(df['content_id'].iloc[rows])
        return ys

    @classmethod
    def get_xys_from_dataframe(cls, df, rows=None):
        """Prepare xys (i.e. a dictionary of named features and labels, e.g.
        xys = {'vif_feat': [0.8, 0.9, 0.5], 'ssim_feat': [1.0, 0.5, 0.6],
        'label': [8.4, 6.5, 4.3]}), which is to be used as input by train(xys),
        from a pandas DataFrame df, e.g.
             ansnr_feat  content_id  distortion_id  ssim_feat     label
        0     0.8           0              0        1.0           8.4
        1     0.9           1              0        0.5           6.5
        0     0.5           0              0        0.6           4.3
        :param df:
        :param rows: if None, take all rows from df, otherwise must be a list of
        row indices
        :return:
        """
        xys = {}
        xys.update(cls.get_xs_from_dataframe(df, rows))
        xys.update(cls.get_ys_from_dataframe(df, rows))
        return xys

class NusvrTrainTestModel(TrainTestModel):

    TYPE = 'nusvr'
    VERSION = "0.1"

    @staticmethod
    def _train(model_param, xys_2d):
        """
        :param model_param:
        :param xys_2d:
        :return:
        """
        kernel = model_param['kernel'] if 'kernel' in model_param else 'rbf'
        degree = model_param['degree'] if 'degree' in model_param else 3
        gamma = model_param['gamma'] if 'gamma' in model_param else 0.0
        coef0 = model_param['coef0'] if 'coef0' in model_param else 0.0
        tol = model_param['tol'] if 'tol' in model_param else 0.001
        C = model_param['C'] if 'C' in model_param else 1.0
        nu = model_param['nu'] if 'nu' in model_param else 0.5
        shrinking = model_param['shrinking'] if 'shrinking' in model_param else True
        cache_size = model_param['cache_size'] if 'cache_size' in model_param else 200
        verbose = model_param['verbose'] if 'verbose' in model_param else False
        max_iter = model_param['max_iter'] if 'max_iter' in model_param else  -1

        from sklearn.svm import NuSVR
        model = NuSVR(kernel=kernel,
                      degree=degree,
                      nu=nu,
                      gamma=gamma,
                      coef0=coef0,
                      tol=tol,
                      C=C,
                      shrinking=shrinking,
                      cache_size=cache_size,
                      verbose=verbose,
                      max_iter=max_iter
                      )
        model.fit(xys_2d[:, 1:], np.ravel(xys_2d[:, 0]))

        return model

class LibsvmnusvrTrainTestModel(TrainTestModel):

    TYPE = 'libsvmnusvr'
    VERSION = "0.1"

    sys.path.append(config.ROOT + "/libsvm/python")
    import svmutil

    # override
    def to_file(self, filename):

        self._assert_trained()

        # special handling of libsvmnusvr: save .model differently
        model_dict_copy = self.model_dict.copy()
        model_dict_copy['model'] = None
        info_to_save = {'param_dict': self.param_dict,
                        'model_dict': model_dict_copy}
        self.svmutil.svm_save_model(filename + '.model', self.model_dict['model'])

        import joblib
        joblib.dump(info_to_save, filename, compress=9)

    # override
    @classmethod
    def from_file(cls, filename, logger):
        train_test_model = cls(param_dict={}, logger=logger)

        import joblib
        info_loaded = joblib.load(filename)
        train_test_model.param_dict = info_loaded['param_dict']
        train_test_model.model_dict = info_loaded['model_dict']

        # special handling of libsvmnusvr: load .model differently
        model = cls.svmutil.svm_load_model(filename + '.model')
        train_test_model.model_dict['model'] = model

        return train_test_model

    # override
    @staticmethod
    def delete(filename):
        if os.path.exists(filename):
            os.remove(filename)
        if os.path.exists(filename + '.model'):
            os.remove(filename + '.model')

    # override
    @classmethod
    def _predict(cls, model, xs_2d):
        f = list(xs_2d)
        for i, item in enumerate(f):
            f[i] = list(item)
        score, _, _ = cls.svmutil.svm_predict([0] * len(f), f, model)
        ys_label_pred = np.array(score)
        return ys_label_pred

    @classmethod
    def _train(cls, model_param, xys_2d):
        """
        :param model_param:
        :param xys_2d:
        :return:
        """
        kernel = model_param['kernel'] if 'kernel' in model_param else 'rbf'
        gamma = model_param['gamma'] if 'gamma' in model_param else 0.0
        C = model_param['C'] if 'C' in model_param else 1.0
        nu = model_param['nu'] if 'nu' in model_param else 0.5
        cache_size = model_param['cache_size'] if 'cache_size' in model_param else 200

        if kernel == 'rbf':
            ktype_int = cls.svmutil.RBF
        elif kernel == 'linear':
            ktype_int = cls.svmutil.LINEAR
        else:
            assert False, 'ktype = ' + str(kernel) + ' not implemented'

        param = cls.svmutil.svm_parameter(['-s', 4,
                                           '-t', ktype_int,
                                           '-c', C,
                                           '-g', gamma,
                                           '-n', nu,
                                           '-m', cache_size])

        f = list(xys_2d[:, 1:])
        for i, item in enumerate(f):
            f[i] = list(item)
        prob = cls.svmutil.svm_problem(xys_2d[:, 0], f)
        model = cls.svmutil.svm_train(prob, param)

        return model

class RandomForestTrainTestModel(TrainTestModel):

    TYPE = 'randomforest'
    VERSION = "0.1"

    @staticmethod
    def _train(model_param, xys_2d):
        """
        random forest regression
        http://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html
        :param model_param:
        :param xys_2d:
        :return:
        """
        n_estimators = model_param['n_estimators'] if 'n_estimators' in model_param else 10
        criterion = model_param['criterion'] if 'criterion' in model_param else 'mse'
        max_depth = model_param['max_depth'] if 'max_depth' in model_param else None
        min_samples_split = model_param['min_samples_split'] if 'min_samples_split' in model_param else 2
        min_samples_leaf = model_param['min_samples_leaf'] if 'min_samples_leaf' in model_param else 1
        max_features = model_param['max_features'] if 'max_features' in model_param else 'auto'
        bootstrap = model_param['bootstrap'] if 'bootstrap' in model_param else True
        oob_score = model_param['oob_score'] if 'oob_score' in model_param else False
        n_jobs = model_param['n_jobs'] if 'n_jobs' in model_param else 1
        random_state = model_param['random_state'] if 'random_state' in model_param else None
        verbose = model_param['verbose'] if 'verbose' in model_param else 0

        from sklearn import ensemble
        model = ensemble.RandomForestRegressor(
            n_estimators=n_estimators,
            criterion=criterion, max_depth=max_depth,
            min_samples_split=min_samples_split,
            min_samples_leaf=min_samples_leaf,
            max_features=max_features,
            bootstrap=bootstrap,
            oob_score=oob_score,
            n_jobs=n_jobs,
            random_state=random_state,
            verbose=verbose
        )
        model.fit(xys_2d[:, 1:], np.ravel(xys_2d[:, 0]))

        return model


class TrainTestModel2(object):

    def __init__(self, param_dict, logger=None):
        """
        :param param_dict: contains model parameters
        :param logger:
        :return:
        """
        self.param_dict = param_dict
        self.logger = logger

    def _assert_trained(self):

        assert hasattr(self, 'model_dict'), \
            "Must train first to generate model_dict."

        # usually get from Result._get_ordered_list_score_key() except for
        # customly constructed
        assert 'ordered_feature_names' in self.model_dict

        assert 'model' in self.model_dict

        assert 'norm_type' in self.model_dict
        norm_type = self.model_dict['norm_type']
        assert (   norm_type == 'none'
                or norm_type == 'normal'
                or norm_type == 'clipped')

        if norm_type == 'normal':
            assert 'mus' in self.model_dict
            assert 'sds' in self.model_dict

        if norm_type == 'clipped':
            assert 'fmins' in self.model_dict
            assert 'fmaxs' in self.model_dict

    def to_file(self, filename):

        self._assert_trained()

        info_to_save = {}
        info_to_save['param_dict'] = self.param_dict
        info_to_save['model_dict'] = self.model_dict

        import joblib
        joblib.dump(info_to_save, filename, compress=9)

    @property
    def ordered_feature_names(self):
        self._assert_trained()
        return self.model_dict['ordered_feature_names']

    @classmethod
    def from_file(cls, filename, logger):

        train_test_model = cls(param_dict={}, logger=logger)

        import joblib
        info_loaded = joblib.load(filename)

        train_test_model.param_dict = info_loaded['param_dict']
        train_test_model.model_dict = info_loaded['model_dict']

        return train_test_model

    # @staticmethod
    # def get_xs_from_results(results, indexs=None):
    #     """
    #     :param results: list of BasicResult
    #     :param indexs: indices of results to be used
    #     :return:
    #     """
    #     feature_names = results[0].get_ordered_list_score_key()
    #     xs = {}
    #     for name in feature_names:
    #         if indexs is None:
    #             _results = results
    #         else:
    #             _results = map(lambda i:results[i], indexs)
    #         xs[name] = np.array(map(lambda result: result[name], _results))
    #     return xs
    #
    # @staticmethod
    # def get_ys_from_results(results, indexs=None):
    #     """
    #     :param results: list of BasicResult
    #     :param indexs: indices of results to be used
    #     :return:
    #     """
    #     ys = {}
    #
    #     if indexs is None:
    #         _results = results
    #     else:
    #         _results = map(lambda i:results[i], indexs)
    #
    #     ys['label'] = \
    #         np.array(map(lambda result: result.asset.groundtruth, _results))
    #     ys['content_id'] = \
    #         np.array(map(lambda result: result.asset.content_id, _results))
    #
    #     return ys
    #
    # @classmethod
    # def get_xys_from_results(cls, results, indexs=None):
    #     """
    #     :param results: list of BasicResult
    #     :param indexs: indices of results to be used
    #     :return:
    #     """
    #     xys = {}
    #     xys.update(cls.get_xs_from_results(results, indexs))
    #     xys.update(cls.get_ys_from_results(results, indexs))
    #     return xys


    # ========================== begin of legacy ===============================

    # below is for the purpose of reading a legacy test text file, and to ensure
    # the other code in the class produces bit-exact results as before

    @staticmethod
    def get_xs_from_dataframe(df, rows=None):
        """Prepare xs (i.e. a dictionary of named features, e.g.
        xs = {'vif_feat': [0.8, 0.9, 0.5], 'ssim_feat': [1.0, 0.5, 0.6]}),
        which is to be used as input by predict(xs), from a pandas DataFrame
        df, e.g.
             ansnr_feat  content_id  distortion_id  ssim_feat     label
        0     0.8           0              0        1.0           8.4
        1     0.9           1              0        0.5           6.5
        0     0.5           0              0        0.6           4.3
        :param df:
        :param rows: if None, take all rows from df, otherwise must be a list of
        row indices
        :return:
        """
        # by the rule of Extraction, features always end with '_feat'
        feature_names = [name for name in df.columns.values if "_feat" in name]
        xs = {}
        for name in feature_names:
            if rows is None:
                xs[name] = np.array(df[name])
            else:
                xs[name] = np.array(df[name].iloc[rows])
        return xs

    @staticmethod
    def get_ys_from_dataframe(df, rows=None):
        """Prepare ys (i.e. a dictionary with key 'label' and labels, e.g.
        ys = {'label': [8.4, 6.5, 4.3]}), from a pandas DataFrame df, e.g.
             ansnr_feat  content_id  distortion_id  ssim_feat     label
        0     0.8           0              0        1.0           8.4
        1     0.9           1              0        0.5           6.5
        0     0.5           0              0        0.6           4.3
        :param df:
        :param rows: if None, take all rows from df, otherwise must be a list of
        row indices
        :return:
        """
        # by the rule of Extraction, labels must have key 'label'
        ys = {}
        if rows is None:
            ys['label'] = np.array(df['label'])
            ys['content_id'] = np.array(df['content_id'])
        else:
            ys['label'] = np.array(df['label'].iloc[rows])
            ys['content_id'] = np.array(df['content_id'].iloc[rows])
        return ys

    @classmethod
    def get_xys_from_dataframe(cls, df, rows=None):
        """Prepare xys (i.e. a dictionary of named features and labels, e.g.
        xys = {'vif_feat': [0.8, 0.9, 0.5], 'ssim_feat': [1.0, 0.5, 0.6],
        'label': [8.4, 6.5, 4.3]}), which is to be used as input by train(xys),
        from a pandas DataFrame df, e.g.
             ansnr_feat  content_id  distortion_id  ssim_feat     label
        0     0.8           0              0        1.0           8.4
        1     0.9           1              0        0.5           6.5
        0     0.5           0              0        0.6           4.3
        :param df:
        :param rows: if None, take all rows from df, otherwise must be a list of
        row indices
        :return:
        """
        xys = {}
        xys.update(cls.get_xs_from_dataframe(df, rows))
        xys.update(cls.get_ys_from_dataframe(df, rows))
        return xys

    # ========================== end of legacy =================================

    @staticmethod
    def _predict(model, xs_2d):

        ys_label_pred = model.predict(xs_2d)

        return ys_label_pred

    def train(self, xys):

        assert 'label' in xys

        ys_vec = xys['label']

        # TODO: modify
        # this makes sure the order of features are normalized, and each
        # dimension of xys_2d is consistent with feature_names

        feature_names = sorted(xys.keys())
        feature_names.remove('label')
        feature_names.remove('content_id')

        # TODO: continue

    def predict(self, xs):

        self._assert_trained()

        for name in self.ordered_feature_names:
            assert name in xs

        xs_2d = []
        for name in self.ordered_feature_names:
            if xs_2d == []:
                xs_2d = np.matrix(xs[name]).T
            else:
                xs_2d = np.hstack((xs_2d, np.matrix(xs[name]).T))
        xs_2d = np.array(xs_2d)

        # normalize xs
        xs_2d = self.normalize_xs(xs_2d)

        # predict
        model = self.model_dict['model']
        ys_label_pred = self._predict(model, xs_2d)

        # denormalize ys
        ys_label_pred = self.denormalize_ys(ys_label_pred)

        return ys_label_pred

    @staticmethod
    def get_stats(ys_label, ys_label_pred):

        import scipy.stats

        # MSE
        mse = np.mean(np.power(np.array(ys_label) - np.array(ys_label_pred), 2.0))

        # spearman
        srcc, _ = scipy.stats.spearmanr(ys_label, ys_label_pred)

        # pearson
        pcc, _ = scipy.stats.pearsonr(ys_label, ys_label_pred)

        # kendall
        kendall, _ = scipy.stats.kendalltau(ys_label, ys_label_pred)

        stats = {}
        stats['MSE'] = mse
        stats['SRCC'] = srcc
        stats['PCC'] = pcc
        stats['KENDALL'] = kendall

        # append raw
        stats['ys_label'] = list(ys_label)
        stats['ys_label_pred'] = list(ys_label_pred)

        return stats

    @classmethod
    def aggregate_stats_list(cls, stats_list):
        aggregate_ys_label = []
        aggregate_ys_label_pred = []
        for stats in stats_list:
            aggregate_ys_label += stats['ys_label']
            aggregate_ys_label_pred += stats['ys_label_pred']
        return cls.get_stats(aggregate_ys_label, aggregate_ys_label_pred)

    @staticmethod
    def get_objective_score(stats, type='SRCC'):
        """
        Objective score is something to MAXIMIZE. e.g. SRCC, or -MSE.
        :param stats:
        :param type:
        :return:
        """
        if type == 'SRCC':
            return stats['SRCC']
        elif type == 'PCC':
            return stats['PCC']
        elif type == 'KENDALL':
            return stats['KENDALL']
        elif type == 'MSE':
            return -stats['MSE']
        else:
            assert False, 'Unknow type: {} for get_objective_score().'.format(type)

    @staticmethod
    def plot_scatter(ax, stats, content_ids=None):
        assert len(stats['ys_label']) == len(stats['ys_label_pred'])

        if content_ids is None:
            ax.scatter(stats['ys_label'], stats['ys_label_pred'])
        else:
            assert len(stats['ys_label']) == len(content_ids)

            unique_content_ids = list(set(content_ids))
            import matplotlib.pyplot as plt
            cmap = plt.get_cmap()
            colors = [cmap(i) for i in np.linspace(0, 1, len(unique_content_ids))]
            for idx, curr_content_id in enumerate(unique_content_ids):
                curr_idxs = indices(content_ids, lambda cid: cid==curr_content_id)
                curr_ys_label = np.array(stats['ys_label'])[curr_idxs]
                curr_ys_label_pred = np.array(stats['ys_label_pred'])[curr_idxs]
                ax.scatter(curr_ys_label, curr_ys_label_pred,
                           label=curr_content_id, color=colors[idx % len(colors)])

    def evaluate(self, xs, ys):
        ys_label_pred = self.predict(xs)
        ys_label = ys['label']

        return self.get_stats(ys_label, ys_label_pred)


class LibsvmnusvrTrainTestModel2(TrainTestModel):

    @classmethod
    def from_raw_file(cls, filename, additional_model_dict):
        """
        Construct from raw libsvm model file.
        :param filename:
        :param additional_model_dict:
        :return:
        """
        pass

    pass

class RandomForestTrainTestModel2(TrainTestModel):
    pass