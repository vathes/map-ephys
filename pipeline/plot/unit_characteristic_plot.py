import numpy as np
import scipy as sp
import datajoint as dj

import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns
import itertools
import pandas as pd

from scipy import signal

from pipeline import experiment, tracking, ephys, psth


def plot_clustering_quality(probe_insert_key):
    amp, snr, spk_times = (ephys.Unit * ephys.ProbeInsertion.InsertionLocation & probe_insert_key).fetch(
        'unit_amp', 'unit_snr', 'spike_times')
    isi_violation, spk_rate = zip(*((compute_isi_violation(spk), compute_spike_rate(spk)) for spk in spk_times))

    metrics = {'amp': amp,
               'snr': snr,
               'isi': np.array(isi_violation),
               'rate': np.array(spk_rate)}
    label_mapper = {'amp': 'Amplitude',
                    'snr': 'Signal to noise ratio (SNR)',
                    'isi': 'ISI violation (%)',
                    'rate': 'Firing rate (spike/s)'}

    fig, axs = plt.subplots(2, 3, figsize=(12, 8))
    fig.subplots_adjust(wspace=0.4)

    for (m1, m2), ax in zip(itertools.combinations(list(metrics.keys()), 2), axs.flatten()):
        ax.plot(metrics[m1], metrics[m2], '.k')
        ax.set_xlabel(label_mapper[m1])
        ax.set_ylabel(label_mapper[m2])

        # cosmetic
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)


def plot_unit_characteristic(probe_insert_key):
    amp, snr, spk_times, x, y, insertion_depth = (ephys.Unit * ephys.ProbeInsertion.InsertionLocation
                                                  & probe_insert_key & 'unit_quality = "good"').fetch(
        'unit_amp', 'unit_snr', 'spike_times', 'unit_posx', 'unit_posy', 'dv_location')

    spk_rate = np.array(list(compute_spike_rate(spk) for spk in spk_times))
    insertion_depth = np.where(np.isnan(insertion_depth), 0, insertion_depth)

    metrics = pd.DataFrame(list(zip(*(amp/amp.max(), snr/snr.max(), spk_rate/spk_rate.max(), x, y + insertion_depth))))
    metrics.columns = ['amp', 'snr', 'rate', 'x', 'y']

    fig, axs = plt.subplots(1, 3, figsize=(10, 8))
    fig.subplots_adjust(wspace=0.6)

    cosmetic = {'legend': None,
                'linewidth': 1.75,
                'alpha': 0.9,
                'facecolor': 'none', 'edgecolor': 'k'}
    m_scale = 1200

    sns.scatterplot(data=metrics, x='x', y='y', s=metrics.amp*m_scale, ax=axs[0], **cosmetic)
    sns.scatterplot(data=metrics, x='x', y='y', s=metrics.snr*m_scale, ax=axs[1], **cosmetic)
    sns.scatterplot(data=metrics, x='x', y='y', s=metrics.rate*m_scale, ax=axs[2], **cosmetic)

    # cosmetic
    for title, ax in zip(('Amplitude', 'SNR', 'Firing rate'), axs.flatten()):
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.set_title(title)
        ax.set_xlim((-10, 60))


def plot_unit_selectivity(probe_insert_key):
    attr_names = ['unit', 'period', 'period_selectivity', 'contra_firing_rate',
                       'ipsi_firing_rate', 'unit_posx', 'unit_posy', 'dv_location']
    selective_units = (psth.UnitSelectivity.PeriodSelectivity * ephys.Unit * ephys.ProbeInsertion.InsertionLocation
                       * experiment.Period & probe_insert_key & 'period_selectivity != "non-selective"').fetch(*attr_names)
    selective_units = pd.DataFrame(selective_units).T
    selective_units.columns = attr_names
    selective_units.period_selectivity.astype('category')

    # --- account for insertion depth (manipulator depth)
    selective_units.unit_posy = (selective_units.unit_posy
                                 + np.where(np.isnan(selective_units.dv_location.values.astype(float)),
                                            0, selective_units.dv_location.values.astype(float)))

    # --- get ipsi vs. contra firing rate difference
    f_rate_diff = np.abs(selective_units.ipsi_firing_rate - selective_units.contra_firing_rate)
    selective_units['f_rate_diff'] = f_rate_diff / f_rate_diff.max()

    # --- prepare for plotting
    m_scale = 1200
    cosmetic = {'legend': None,
                'linewidth': 0.0001}
    ymax = selective_units.unit_posy.max() + 100

    # a bit of hack to get 'open circle'
    pts = np.linspace(0, np.pi * 2, 24)
    circ = np.c_[np.sin(pts) / 2, -np.cos(pts) / 2]
    vert = np.r_[circ, circ[::-1] * .7]

    open_circle = mpl.path.Path(vert)

    # --- plot
    fig, axs = plt.subplots(1, 3, figsize=(10, 8))
    fig.subplots_adjust(wspace=0.6)
    for (title, df), ax in zip(((p, selective_units[selective_units.period == p])
                                for p in ('sample', 'delay', 'response')), axs):
        sns.scatterplot(data=df, x='unit_posx', y='unit_posy',
                        s=df.f_rate_diff.values.astype(float)*m_scale,
                        hue='period_selectivity', marker=open_circle,
                        palette={'contra-selective': 'b', 'ipsi-selective': 'r'},
                        ax=ax, **cosmetic)
        contra_p = (df.period_selectivity == 'contra-selective').sum() / len(df) * 100
        # cosmetic
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.set_title(f'{title}\n% contra: {contra_p:.2f}\n% ipsi: {100-contra_p:.2f}')
        ax.set_xlim((-10, 60))
        ax.set_ylim((0, ymax))


def plot_stacked_contra_ipsi_psth(probe_insert_key, axs=None):
    unit_hemi = (ephys.ProbeInsertion.InsertionLocation * experiment.BrainLocation
                 & probe_insert_key).fetch1('hemisphere')

    contra_trials = psth.TrialCondition & {'condition_id': 0 if unit_hemi == 'left' else 1}
    ipsi_trials = psth.TrialCondition & {'condition_id': 1 if unit_hemi == 'left' else 0}


    ipsi_sel_units = ephys.Unit * psth.UnitSelectivity & 'unit_selectivity = "ipsi-selective"'
    contra_sel_units = ephys.Unit * psth.UnitSelectivity & 'unit_selectivity = "contra-selective"'

    if axs is None:
        fig, axs = plt.subplots(1, 2, figsize=(8, 12))

    assert axs.size == 2

    # contra-selective units
    plot_stacked_psth_diff(
        (contra_sel_units & contra_trials).fetch(order_by='unit_posy asc'),
        (contra_sel_units & ipsi_trials).fetch(order_by='unit_posy asc'),
        ax=axs[0])
    plot_stacked_psth_diff(
        (ipsi_sel_units & ipsi_trials).fetch(order_by='unit_posy asc'),
        (ipsi_sel_units & contra_trials).fetch(order_by='unit_posy asc'),
        ax=axs[0])



def plot_stacked_psth_diff(psth_a, psth_b, ax=None):
    """
    Heatmap of (psth_a - psth_b)
    psth_a, psth_b are the unit_psth(s) resulted from psth.UnitPSTH.fetch()
    """
    plt_xmin, plt_xmax = -3, 3

    assert len(psth_a) == len(psth_b)
    nunits = len(psth_a)
    aspect = 2 / nunits
    extent = [plt_xmin, plt_xmax, 0, nunits]

    a_data = np.array([r[0] for r in psth_a['unit_psth']])
    b_data = np.array([r[0] for r in psth_b['unit_psth']])

    # scale per-unit psth's - TODO: moving average scaling
    a_data = np.array([movmean(i * (1 / np.max(i))) for i in a_data])
    b_data = np.array([movmean(i * (1 / np.max(i))) for i in b_data])

    result = a_data - b_data

    if ax is None:
        fig, ax = plt.subplots(1, 1)

    # ax.set_axis_off()
    ax.set_xlim([plt_xmin, plt_xmax])
    ax.axvline(0, 0, 1, ls = '--', color = 'k')
    ax.axvline(-1.2, 0, 1, ls = '--', color = 'k')
    ax.axvline(-2.4, 0, 1, ls = '--', color = 'k')

    ax.imshow(result, cmap=plt.cm.bwr, aspect=aspect, extent=extent)


def compute_isi_violation(spike_times, isi_thresh=2):
    isi = np.diff(spike_times)
    return sum((isi < isi_thresh).astype(int)) / len(isi)


def compute_spike_rate(spike_times):
    return len(spike_times) / (spike_times[-1] - spike_times[0])


def movmean(data, nsamp=5):
    ret = np.cumsum(data, dtype=float)
    ret[nsamp:] = ret[nsamp:] - ret[:-nsamp]
    return ret[nsamp - 1:] / nsamp