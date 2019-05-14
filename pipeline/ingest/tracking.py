
import os
import logging
from glob import glob

import numpy as np
import datajoint as dj

from pipeline import lab
from pipeline import tracking
from pipeline import experiment
from pipeline.ingest import behavior as ingest_behavior
[ingest_behavior]  # NOQA schema only use

from collections import defaultdict

log = logging.getLogger(__name__)


schema = dj.schema(dj.config['custom'].get(
    'ingest.tracking.database',
    '{}_ingestTracking'.format(dj.config['database.user'])))


@schema
class TrackingDataPath(dj.Lookup):
    # ephys data storage location(s)
    definition = """
    -> lab.Rig
    tracking_data_path:         varchar(255)            # rig data path
    """

    @property
    def contents(self):
        if 'tracking_data_paths' in dj.config:  # for local testing
            return dj.config['tracking_data_paths']

        return [('RRig', r'H:\\data\MAP',)]


@schema
class TrackingIngest(dj.Imported):
    definition = """
    -> ingest_behavior.BehaviorIngest
    """

    class TrackingFile(dj.Part):
        definition = '''
        -> TrackingIngest
        -> experiment.SessionTrial
        ---
        tracking_file:          varchar(255)            # tracking file subpath
        '''

    def make(self, key):
        '''
        TrackingIngest .make() function
        '''
        log.info('TrackingIngest().make(): key: {k}'.format(k=key))

        h2o = (lab.WaterRestriction() & key).fetch1('water_restriction_number')
        session = (experiment.Session() & key).fetch1()
        trials = (experiment.SessionTrial() & session).fetch(as_dict=True)

        sdate = session['session_date']
        sdate_iso = sdate.isoformat()  # YYYY-MM-DD
        sdate_sml = "{}{:02d}{:02d}".format(sdate.year, sdate.month, sdate.day)

        paths = TrackingDataPath.fetch(as_dict=True)
        devices = tracking.TrackingDevice().fetch(as_dict=True)

        # paths like: <root>/<h2o>/YYYY-MM-DD/tracking
        for p, d in ((p, d) for d in devices for p in paths):

            tdev = d['tracking_device']
            tpos = d['tracking_position']
            tdat = p['tracking_data_path']

            log.info('key', key)  # subject_id sesssion
            log.debug('trying {}'.format(tdat))
            log.debug('got session: {}'.format(session))
            log.debug('got trials: {}'.format(trials))

            tpath = os.path.join(tdat, h2o, sdate_iso, 'tracking')

            if not os.path.exists(tpath):
                log.info('tracking path {} n/a - skipping'.format(tpath))
                continue

            camtrial = '{}_{}_{}.txt'.format(h2o, sdate_sml, tpos)
            campath = os.path.join(tpath, camtrial)

            log.debug('trying camera position trial map: {}'.format(campath))

            if not os.path.exists(campath):
                log.info('skipping {} - does not exist'.format(campath))
                continue

            tmap = self.load_campath(campath)

            for t in tmap:  # load tracking for trial
                # ex: dl59_side_1-0000.csv / h2o_position_tn-0000.csv
                tfile = '{}_{}_{}-*.csv'.format(h2o, tpos, t)
                tfull = os.path.join(tpath, tfile)
                tfull = glob(tfull)

                if not tfull or len(tfull) > 1:
                    log.info('tracking file {} mismatch'.format(tfull))
                    continue

                tfull = tfull[-1]
                trk = self.load_tracking(tfull)

                recs = {}
                rec_base = dict(key, trial=tmap[t], tracking_device=tdev)

                for k in trk:
                    if k == 'samples':
                        recs['tracking'] = {
                            **rec_base,
                            'tracking_samples': len(trk['samples']['ts']),
                        }
                    else:
                        rec = dict(rec_base)

                        for attr in trk[k]:
                            rec_key = '{}_{}'.format(k, attr)
                            rec[rec_key] = np.array(trk[k][attr])

                        recs[k] = rec

                tracking.Tracking.insert1(
                    recs['tracking'], allow_direct_insert=True)

                tracking.Tracking.NoseTracking.insert1(
                    recs['nose'], allow_direct_insert=True)

                tracking.Tracking.TongueTracking.insert1(
                    recs['tongue'], allow_direct_insert=True)

                tracking.Tracking.JawTracking.insert1(
                    recs['jaw'], allow_direct_insert=True)

            self.insert1(key)


    def load_campath(self, campath):
        ''' load camera position-to-trial map '''
        log.debug("load_campath(): {}".format(campath))
        with open(campath, 'r') as f:
            return {int(k): int(v) for i in f
                    for k, v in (i.strip().split('\t'),)}

    def load_tracking(self, trkpath):
        log.info('load_tracking() {}'.format(trkpath))
        '''
        load actual tracking data.

        example format:

        scorer,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000,DeepCut_resnet50_licking-sideAug10shuffle1_1030000
        bodyparts,nose,nose,nose,tongue,tongue,tongue,jaw,jaw,jaw
        coords,x,y,likelihood,x,y,likelihood,x,y,likelihood
        0,418.48327827453613,257.231650352478,1.0,426.47182297706604,263.82502603530884,1.796432684386673e-06,226.12365770339966,395.8081398010254,1.0

        results are of the form:

          {'feature': {'attr': [val, ...]}}

        where feature is e.g. 'nose', 'attr' is e.g. 'x'.

        the special 'feature'/'attr' pair "samples"/"ts" is used to store
        the first column/sample timestamp for each row in the input file.
        '''
        res = defaultdict(lambda: defaultdict(list))

        with open(trkpath, 'r') as f:
            f.readline()  # discard 1st line
            parts, fields = f.readline(), f.readline()
            parts = parts.rstrip().split(',')
            fields = fields.rstrip().split(',')

            for l in f:
                lv = l.rstrip().split(',')
                for i, v in enumerate(lv):
                    v = float(v)
                    if i == 0:
                        res['samples']['ts'].append(v)
                    else:
                        res[parts[i]][fields[i]].append(v)

        return res
