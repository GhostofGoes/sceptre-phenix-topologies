import os, shutil, socket, stat, time

from phenix_apps.apps   import AppBase
from phenix_apps.common import utils
from phenix_apps.common.logger import logger

import minimega


class Windy(AppBase):
    def __init__(self, name, stage, dryrun=False):
        super().__init__(name, stage, dryrun)

        self.templates = utils.abs_path(__file__, 'templates')

    def post_start(self):
        logger.info(f'Provisioning (post-start) user application: {self.name}')

        if self.dryrun:
            logger.info(f'Skipping post-start for {self.name}: DRY-RUN')
            return

        if not self.metadata: return

        md = self.metadata.get('soar', None)
        if not md: return

        # Determine what cluster host the VM to be disconnected is on
        vm   = md.get('disconnect', 'kali-ot,ot').split(',')
        vlan = None
        host = None

        if len(vm) == 1:
            vlan = 'ot'
        else:
            vlan = vm[1]
            vm   = vm[0]

        try:
            host = self.experiment.status.schedules[vm]
        except:
            return

        # Determine the tap name for the VM's EXP interface
        mm   = minimega.connect(namespace=self.exp_name)
        info = mm.vm_info()
        tap  = None

        # `info` will be an array of objects, each representing cluster hosts
        # with VMs scheduled on them. Each cluster host object will have a
        # 'Data' key, whose value is an array of objects, each representing a
        # VM scheduled on the host.
        for h in info:
            if h["Host"] != host: continue

            for d in h["Data"]:
                if d["Name"] != vm: continue

                for n in d["Networks"]:
                    if n["Alias"] != vlan: continue

                    tap = n["Tap"]
                    break

                break

            break

        if not tap:
            # This might happen if soar metadata was present in the scenario
            # configuration but the topology doesn't have the "disconnect" node.
            return

        # Create script that will remove the VM's EXP interface from phenix bridge
        script_file = f'/phenix/images/{self.exp_name}/disconnect-kali.sh'

        with open(script_file, 'w') as f:
            utils.mako_serve_template('disconnect-kali.mako', self.templates, f, tap=tap)

        stats = os.stat(script_file)
        os.chmod(script_file, stats.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # minimega cannot mesh send to its own node, so when the VM runs on this
        # host (a single-node deployment) the same steps run here instead.
        local = host == socket.gethostname()

        # Copy script to cluster host and move it to /usr/local/bin/disconnect-kali.sh
        if local:
            shutil.move(script_file, '/usr/local/bin/disconnect-kali.sh')
        else:
            mm.mesh_send(host, f'file get {self.exp_name}/disconnect-kali.sh')
            time.sleep(5) # (overkill) give file time to copy since `file get` doesn't block
            mm.mesh_send(host, f'shell mv /phenix/images/{self.exp_name}/disconnect-kali.sh /usr/local/bin/disconnect-kali.sh')

        # Create tap for cluster host on configured VLAN with configured IP
        ip   = md.get('tap', 'MGMT,172.16.60.51/16').split(',')
        vlan = 'MGMT'

        if len(ip) == 1:
            ip   = ip[0]
        else:
            vlan = ip[0]
            ip   = ip[1]

        if local:
            mm.tap_create_bridge_ip(f'{self.exp_name}//{vlan}', 'phenix', ip, 'soar')
        else:
            mm.mesh_send(host, f'tap create {self.exp_name}//{vlan} bridge phenix ip {ip} soar')

        logger.info(f'Provisioned (post-start) user application: {self.name}')


    def cleanup(self):
        logger.info(f'Cleaning up user application: {self.name}')

        if self.dryrun:
            logger.info(f'Skipping cleanup for {self.name}: DRY-RUN')
            return

        if not self.metadata: return

        md = self.metadata.get('soar', None)
        if not md: return

        vm = md.get('disconnect', 'kali-ot,ot').split(',')[0]

        try:
            host = self.experiment.status.schedules[vm]
        except:
            return

        # post-start created the tap in the experiment's namespace
        mm    = minimega.connect(namespace=self.exp_name)
        local = host == socket.gethostname()

        try:
            if local:
                os.remove('/usr/local/bin/disconnect-kali.sh')
            else:
                mm.mesh_send(host, f'shell rm /usr/local/bin/disconnect-kali.sh')
        except: # an exception will be thrown if the file doesn't exist on the remote host
            pass

        try:
            if local:
                mm.tap_delete('soar')
            else:
                mm.mesh_send(host, f'tap delete soar')
        except: # an exception will be thrown if a tap with the name 'soar' doesn't exist
            pass

        logger.info(f'Cleaned up user application: {self.name}')


def main():
    Windy.main('windy')


if __name__ == '__main__':
    main()
