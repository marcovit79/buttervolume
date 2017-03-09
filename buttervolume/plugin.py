import csv
import json
import logging
import os
from bottle import request, route
from buttervolume import btrfs
from datetime import datetime
from os.path import join, basename, exists, dirname
from subprocess import check_call
from subprocess import run, PIPE
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger()

# absolute path to the volumes
VOLUMES_PATH = "/var/lib/docker/volumes/"
SNAPSHOTS_PATH = "/var/lib/docker/snapshots/"
TEST_RECEIVE_PATH = "/var/lib/docker/received/"
SCHEDULE = "/etc/buttervolume/schedule.csv"
PATH_STORE = "/etc/buttervolume/volumepath.csv"
SCHEDULE_LOG = {'snapshot': {}, 'replicate': {}}


def jsonloads(stuff):
    return json.loads(stuff.decode())


@route('/Plugin.Activate', ['POST'])
def plugin_activate():
    return json.dumps({'Implements': ['VolumeDriver']})


def _volume_path(name, path=None):
    """Simple CRUD for volume paths
    if path is None: returns the stored path or the standard one
    if path is '': delete the stored path
    if path is specified, store it
    """
    if path is None:  # READ
        if os.path.exists(PATH_STORE):
            with open(PATH_STORE) as f:
                for v, p in csv.reader(f):
                    if v == name:
                        return join(p, v)
        return join(VOLUMES_PATH, name)
    if path == '':  # DELETE
        paths = []
        if os.path.exists(PATH_STORE):
            with open(PATH_STORE) as f:
                for v, p in csv.reader(f):
                    if v == name:
                        continue
        os.makedirs(dirname(PATH_STORE), exist_ok=True)
        with open(PATH_STORE, 'w') as f:
            for line in paths:
                csv.writer(f).writerow(line)
        return join(VOLUMES_PATH, name)
    # UPDATE or CREATE
    paths = []
    if os.path.exists(PATH_STORE):
        with open(PATH_STORE) as f:
            for v, p in csv.reader(f):
                if v == name:
                    continue
                paths.append((v, p))
            paths.append((name, path))
    os.makedirs(dirname(PATH_STORE), exist_ok=True)
    with open(PATH_STORE, 'w') as f:
        for line in paths:
            csv.writer(f).writerow(line)
    return join(path, name)


@route('/VolumeDriver.Create', ['POST'])
def volume_create():
    request_data = jsonloads(request.body.read())
    name = request_data['Name']
    options = request_data.get('Opts')
    volumes_path = options and options.get('VolumesPath')
    if volumes_path:  # store it
        _volume_path(name, volumes_path)
    if '@' in name:
        return json.dumps({'Err': '"@" is illegal in the name of the volume'})
    # volume already exists?
    if name in [v['Name']for v in json.loads(volume_list())['Volumes']]:
        return json.dumps({'Err': ''})
    try:
        btrfs.Subvolume(_volume_path(name)).create()
    except Exception as e:
        return {'Err': e.strerror}
    return json.dumps({'Err': ''})


@route('/VolumeDriver.Mount', ['POST'])
def volume_mount():
    name = jsonloads(request.body.read())['Name']
    path = _volume_path(name)
    if exists(join(path, '_data', '.nocow')) or exists(join(path, '.nocow')):
        try:
            check_call("chattr +C '{}'".format(join(path)), shell=True)
            log.info("disabled COW on %s", path)
        except Exception:
            return json.dumps(
                {'Err': 'could not disable COW on {}'.format(path)})
    if exists(join(path, '_data', '.nocow')):
        os.remove(join(path, '_data', '.nocow'))
    if exists(join(path, '.nocow')):
        os.remove(join(path, '.nocow'))
    return volume_path()


@route('/VolumeDriver.Path', ['POST'])
def volume_path():
    name = jsonloads(request.body.read())['Name']
    path = _volume_path(name)
    try:
        btrfs.Subvolume(path).show()
    except Exception:
        return json.dumps({'Err': '{}: no such volume'.format(path)})
    return json.dumps({'Mountpoint': path, 'Err': ''})


@route('/VolumeDriver.Unmount', ['POST'])
def volume_unmount():
    return json.dumps({'Err': ''})


@route('/VolumeDriver.Get', ['POST'])
def volume_get():
    name = jsonloads(request.body.read())['Name']
    path = _volume_path(name)
    try:
        btrfs.Subvolume(path).show()
    except Exception:
        return json.dumps({'Err': '{}: no such volume'.format(path)})
    return json.dumps(
        {'Volume': {'Name': name, 'Mountpoint': path}, 'Err': ''})


@route('/VolumeDriver.Remove', ['POST'])
def volume_remove():
    name = jsonloads(request.body.read())['Name']
    path = _volume_path(name)
    try:
        btrfs.Subvolume(path).delete()
    except Exception:
        return json.dumps({'Err': '{}: no such volume'.format(name)})
    return json.dumps({'Err': ''})


@route('/VolumeDriver.List', ['POST'])
def volume_list():
    volumes = []
    specials = []
    if os.path.exists(PATH_STORE):
        with open(PATH_STORE) as f:
            specials = [join(p, v) for v, p in csv.reader(f)]
    for p in [join(VOLUMES_PATH, v) for v in os.listdir(VOLUMES_PATH)
              if v != 'metadata.db'] + specials:
        try:
            btrfs.Subvolume(p).show()
        except Exception:
            continue
        volumes.append(p)
    return json.dumps({'Volumes': [{'Name': basename(v)} for v in volumes],
                       'Err': ''})


@route('/VolumeDriver.Snapshot.Send', ['POST'])
def snapshot_send():
    """The last sent snapshot is remembered by adding a suffix with the target
    """
    test = jsonloads(request.body.read()).get('Test', False)
    snapshot_name = jsonloads(request.body.read())['Name']
    snapshot_path = join(SNAPSHOTS_PATH, snapshot_name)
    remote_host = jsonloads(request.body.read())['Host']
    remote_snapshots = SNAPSHOTS_PATH if not test else TEST_RECEIVE_PATH
    # take the latest snapshot suffixed with the target host
    sent_snapshots = sorted(
        [s for s in os.listdir(SNAPSHOTS_PATH)
         if len(s.split('@')) == 3
         and s.split('@')[0] == snapshot_name.split('@')[0]
         and s.split('@')[2] == remote_host])
    latest = sent_snapshots[-1] if len(sent_snapshots) > 0 else None
    if latest and len(latest.rsplit('@')) == 3:
        latest = latest.rsplit('@', 1)[0]
    parent = '-p "{}"'.format(join(SNAPSHOTS_PATH, latest)) if latest else ''
    port = '1122'
    if test:  # I currently run tests outside docker
        port = '22'
    run('sync', shell=True)  # needed by a current issue with send
    cmd = ('btrfs send {parent} "{snapshot_path}"'
           ' | ssh -p {port} {remote_host} "btrfs receive {remote_snapshots}"')
    try:
        log.info(cmd.format(**locals()))
        run(cmd.format(**locals()),
            shell=True, check=True, stdout=PIPE, stderr=PIPE)
    except:
        log.warn('Failed using parent %s. Sending full snapshot %s',
                 latest, snapshot_path)
        parent = ''
        try:
            rmcmd = (
                'ssh -p {port} {remote_host} '
                '"btrfs subvolume delete {remote_snapshots}/{snapshot_name}"')
            log.info(rmcmd.format(**locals()))
            run(rmcmd.format(**locals()), shell=True, stdout=PIPE, stderr=PIPE)
            log.info(cmd.format(**locals()))
            run(cmd.format(**locals()),
                shell=True, check=True, stdout=PIPE, stderr=PIPE)
        except Exception as e:
            return json.dumps({'Err': str(e)})
    btrfs.Subvolume(snapshot_path).snapshot(
        '{}@{}'.format(snapshot_path, remote_host), readonly=True)
    for old_snapshot in sent_snapshots:
        btrfs.Subvolume(old_snapshot).delete
    return json.dumps({'Err': ''})


@route('/VolumeDriver.Snapshot', ['POST'])
def volume_snapshot():
    """snapshot a volume in the SNAPSHOTS dir
    """
    name = jsonloads(request.body.read())['Name']
    path = _volume_path(name)
    timestamped = '{}@{}'.format(name, datetime.now().isoformat())
    snapshot_path = join(SNAPSHOTS_PATH, timestamped)
    if not os.path.exists(path):
        return json.dumps({'Err': 'No such volume'})
    try:
        btrfs.Subvolume(path).snapshot(snapshot_path, readonly=True)
    except Exception as e:
        return {'Err': str(e)}
    return json.dumps({'Err': '', 'Snapshot': timestamped})


@route('/VolumeDriver.Snapshot.List', ['POST'])
def snapshot_list():
    name = jsonloads(request.body.read()).get('Name')
    snapshots = os.listdir(SNAPSHOTS_PATH)
    if name:
        snapshots = [s for s in snapshots if s.startswith(name + '@')]
    return json.dumps({'Err': '', 'Snapshots': snapshots})


@route('/VolumeDriver.Snapshot.Remove', ['POST'])
def snapshot_delete():
    name = jsonloads(request.body.read())['Name']
    path = join(SNAPSHOTS_PATH, name)
    if not os.path.exists(path):
        return json.dumps({'Err': 'No such snapshot'})
    try:
        btrfs.Subvolume(path).delete()
    except Exception as e:
        return {'Err': str(e)}
    return json.dumps({'Err': ''})


@route('/VolumeDriver.Schedule', ['POST'])
def schedule():
    """Schedule or unschedule a job
    TODO add a lock
    """
    name = jsonloads(request.body.read())['Name']
    timer = jsonloads(request.body.read())['Timer']
    action = jsonloads(request.body.read())['Action']
    schedule = []
    if timer:  # 0 means unschedule!
        schedule.append((name, action, timer))
    if os.path.exists(SCHEDULE):
        with open(SCHEDULE) as f:
            for n, a, t in csv.reader(f):
                # skip the line we want to write
                if n == name and a == action:
                    continue
                schedule.append((n, a, t))
    os.makedirs(dirname(SCHEDULE), exist_ok=True)
    with open(SCHEDULE, 'w') as f:
        for line in schedule:
            csv.writer(f).writerow(line)
    return json.dumps({'Err': ''})


@route('/VolumeDriver.Schedule.List', ['GET'])
def schedule_list():
    """List scheduled jobs
    """
    schedule = []
    if os.path.exists(SCHEDULE):
        with open(SCHEDULE) as f:
            for n, a, t in csv.reader(f):
                schedule.append({'Name': n, 'Timer': t, 'Action': a})
    return json.dumps({'Err': '', 'Schedule': schedule})


@route('/VolumeDriver.Snapshot.Restore', ['POST'])
def snapshot_restore():
    """
    Snapshot a volume and overwrite it with the specified snapshot.
    """
    snapshot_name = jsonloads(request.body.read())['Name']
    if '@' not in snapshot_name:
        # we're passing the name of the volume. Use the latest snapshot.
        volume_name = snapshot_name
        snapshots = os.listdir(SNAPSHOTS_PATH)
        snapshots = [s for s in snapshots if s.startswith(volume_name + '@')]
        if not snapshots:
            return {'Err': ''}
        snapshot_name = sorted(snapshots)[-1]
    snapshot_path = join(SNAPSHOTS_PATH, snapshot_name)
    snapshot = btrfs.Subvolume(snapshot_path)
    volume_name = snapshot_name.split('@')[0]
    volume_path = _volume_path(volume_name)
    volume = btrfs.Subvolume(volume_path)
    res = {'Err': ''}
    if snapshot.exists():
        if volume.exists():
            # backup and delete
            timestamp = datetime.now().isoformat()
            stamped_name = '{}@{}'.format(volume_name, timestamp)
            stamped_path = join(SNAPSHOTS_PATH, stamped_name)
            volume.snapshot(stamped_path, readonly=True)
            res['VolumeBackup'] = stamped_name
            volume.delete()
        snapshot.snapshot(volume_path)
    else:
        res['Err'] = 'No such snapshot'
    return json.dumps(res)
