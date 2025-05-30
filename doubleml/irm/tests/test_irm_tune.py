import math

import numpy as np
import pytest
from sklearn.base import clone
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression

import doubleml as dml

from ...tests._utils import draw_smpls
from ._utils_irm_manual import boot_irm, fit_irm, tune_nuisance_irm


@pytest.fixture(scope="module", params=[RandomForestRegressor(random_state=42)])
def learner_g(request):
    return request.param


@pytest.fixture(scope="module", params=[LogisticRegression()])
def learner_m(request):
    return request.param


@pytest.fixture(scope="module", params=["ATE", "ATTE"])
def score(request):
    return request.param


@pytest.fixture(scope="module", params=[True, False])
def normalize_ipw(request):
    return request.param


@pytest.fixture(scope="module", params=[True, False])
def tune_on_folds(request):
    return request.param


def get_par_grid(learner):
    if learner.__class__ in [RandomForestRegressor]:
        par_grid = {"n_estimators": [5, 10, 20]}
    else:
        assert learner.__class__ in [LogisticRegression]
        par_grid = {"C": np.logspace(-4, 2, 10)}
    return par_grid


@pytest.fixture(scope="module")
def dml_irm_fixture(generate_data_irm, learner_g, learner_m, score, normalize_ipw, tune_on_folds):
    par_grid = {"ml_g": get_par_grid(learner_g), "ml_m": get_par_grid(learner_m)}
    n_folds_tune = 4

    boot_methods = ["normal"]
    n_folds = 2
    n_rep_boot = 499

    # collect data
    (x, y, d) = generate_data_irm
    n_obs = len(y)
    all_smpls = draw_smpls(n_obs, n_folds, n_rep=1, groups=d)

    # Set machine learning methods for m & g
    ml_g = clone(learner_g)
    ml_m = clone(learner_m)

    np.random.seed(3141)
    obj_dml_data = dml.DoubleMLData.from_arrays(x, y, d)
    dml_irm_obj = dml.DoubleMLIRM(obj_dml_data, ml_g, ml_m, n_folds, score=score, normalize_ipw=normalize_ipw)
    # synchronize the sample splitting
    dml_irm_obj.set_sample_splitting(all_smpls=all_smpls)
    np.random.seed(3141)
    # tune hyperparameters
    tune_res = dml_irm_obj.tune(par_grid, tune_on_folds=tune_on_folds, n_folds_tune=n_folds_tune, return_tune_res=False)
    assert isinstance(tune_res, dml.DoubleMLIRM)

    dml_irm_obj.fit()

    np.random.seed(3141)
    smpls = all_smpls[0]

    if tune_on_folds:
        g0_params, g1_params, m_params = tune_nuisance_irm(
            y, x, d, clone(learner_g), clone(learner_m), smpls, score, n_folds_tune, par_grid["ml_g"], par_grid["ml_m"]
        )
    else:
        xx = [(np.arange(len(y)), np.array([]))]
        g0_params, g1_params, m_params = tune_nuisance_irm(
            y, x, d, clone(learner_g), clone(learner_m), xx, score, n_folds_tune, par_grid["ml_g"], par_grid["ml_m"]
        )
        g0_params = g0_params * n_folds
        m_params = m_params * n_folds
        if score == "ATE":
            g1_params = g1_params * n_folds
        else:
            assert score == "ATTE"
            g1_params = None

    res_manual = fit_irm(
        y,
        x,
        d,
        clone(learner_g),
        clone(learner_m),
        all_smpls,
        score,
        normalize_ipw=normalize_ipw,
        g0_params=g0_params,
        g1_params=g1_params,
        m_params=m_params,
    )

    res_dict = {
        "coef": dml_irm_obj.coef.item(),
        "coef_manual": res_manual["theta"],
        "se": dml_irm_obj.se.item(),
        "se_manual": res_manual["se"],
        "boot_methods": boot_methods,
    }

    for bootstrap in boot_methods:
        np.random.seed(3141)
        boot_t_stat = boot_irm(
            y,
            d,
            res_manual["thetas"],
            res_manual["ses"],
            res_manual["all_g_hat0"],
            res_manual["all_g_hat1"],
            res_manual["all_m_hat"],
            res_manual["all_p_hat"],
            all_smpls,
            score,
            bootstrap,
            n_rep_boot,
            normalize_ipw=normalize_ipw,
        )

        np.random.seed(3141)
        dml_irm_obj.bootstrap(method=bootstrap, n_rep_boot=n_rep_boot)
        res_dict["boot_t_stat" + bootstrap] = dml_irm_obj.boot_t_stat
        res_dict["boot_t_stat" + bootstrap + "_manual"] = boot_t_stat.reshape(-1, 1, 1)

    return res_dict


@pytest.mark.ci
def test_dml_irm_coef(dml_irm_fixture):
    assert math.isclose(dml_irm_fixture["coef"], dml_irm_fixture["coef_manual"], rel_tol=1e-9, abs_tol=1e-4)


@pytest.mark.ci
def test_dml_irm_se(dml_irm_fixture):
    assert math.isclose(dml_irm_fixture["se"], dml_irm_fixture["se_manual"], rel_tol=1e-9, abs_tol=1e-4)


@pytest.mark.ci
def test_dml_irm_boot(dml_irm_fixture):
    for bootstrap in dml_irm_fixture["boot_methods"]:
        assert np.allclose(
            dml_irm_fixture["boot_t_stat" + bootstrap],
            dml_irm_fixture["boot_t_stat" + bootstrap + "_manual"],
            rtol=1e-9,
            atol=1e-4,
        )
