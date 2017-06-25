#!/usr/bin/env python

'''
Atlantic.Net external inventory script
======================================

Generates Ansible inventory of Atlantic.Net Cloudservers.

In addition to the --list and --host options used by Ansible, there are options
for generating JSON of other Atlantic.Net data.  This is useful when creating
cloudservers.  For example, --plans will return all the Atlantic.Net Plans.
This information can also be easily found in the cache file, whose default
location is /tmp/ansible-digital_ocean.cache).

The --pretty (-p) option pretty-prints the output for better human readability.

----
Although the cache stores all the information received from Atlantic.Net,
the cache is not used for current cloudserver information (in --list, --host,
--all, and --cloudservers).  This is so that accurate cloudserver information is always
found.  You can force this script to use the cache with --force-cache.

----
Configuration is read from `anet_inventory.ini`, then from environment variables,
then and command-line arguments.

Most notably, the Atlantic.Net Public Key and Private Key must both be specified.
It can be specified in the INI file or with the following environment variables:
    export ANET_PUBLIC_KEY='abc123' and
    export ANET_PRIVATE_KEY='abc123'

Alternatively, it can be passed on the command-line with --api-token.

If you specify Atlantic.Net credentials in the INI file, a handy way to
get them into your environment (e.g., to use the anet_inventory module)
is to use the output of the --env option with export:
    export $(anet_inventory.py --env)

----
The following groups are generated from --list:
 - ID    (Cloud Server ID)
 - NAME  (Cloud Server NAME)
 - image_ID
 - image_NAME
 - distro_NAME  (distribution NAME from image)
 - plan_NAME
 - status_STATUS

When run against a specific host, this script returns the following variables:

 - anet_cloned_from
 - anet_cu_id
 - anet_disallow_deletion
 - anet_InstanceId
 - anet_rate_per_hr
 - anet_remove
 - anet_reprovisioning_processed_date
 - anet_resetpwd_processed_date
 - anet_vm_bandwidth
 - anet_vm_cpu_req
 - anet_vm_created_date
 - anet_vm_description
 - anet_vm_disk_req
 - anet_vm_id
 - anet_vm_image
 - anet_vm_image_display_name
 - anet_vm_ip_address
 - anet_vm_ip_gateway
 - anet_vm_ip_subnet
 - anet_vm_network_req
 - anet_vm_os_architecture
 - anet_vm_plan_name
 - anet_vm_ram_req
 - anet_vm_removed_date
 - anet_vm_status
 - anet_vm_username
 - anet_vm_vnc_password
 - anet_vnc_port

-----
```
usage: anet_inventory.py [-h] [--list] [--host HOST] [--all]
                                 [--cloudservers] [--images] [--plans]
                                 [--ssh-keys] [--pretty]
                                 [--cache-path CACHE_PATH]
                                 [--cache-max_age CACHE_MAX_AGE]
                                 [--force-cache]
                                 [--refresh-cache]
                                 [--public_key ANET_PUBLIC_KEY]
                                 [--private_key ANET_PRIVATE_KEY]

Produce an Ansible Inventory file based on Atlantic.Net credentials

optional arguments:
  -h, --help            show this help message and exit
  --list                List all active Cloudservers as Ansible inventory
                        (default: True)
  --host HOST           Get all Ansible inventory variables about a specific
                        Cloudserver
  --all                 List all Atlantic.Net information as JSON
  --cloudservers, -c    List Cloudservers as JSON
  --images              List Images as JSON
  --plans               List Plans as JSON
  --ssh-keys            List SSH keys as JSON
  --pretty, -p          Pretty-print results
  --cache-path CACHE_PATH
                        Path to the cache files (default: .)
  --cache-max_age CACHE_MAX_AGE
                        Maximum age of the cached items (default: 0)
  --force-cache         Only use data from the cache
  --refresh-cache       Force refresh of cache by making API requests to
                        Atlantic.Net (default: False - use cache files)
  --public_key ANET_PUBLIC_KEY, -a ANET_PUBLIC_KEY
                        Atlantic.Net Public Key
  --private_key ANET_PRIVATE_KEY, -b ANET_PRIVATE_KEY
                        Atlantic.Net Private Key
```

'''

# (c) 2017, Cailen LeTigre <github.com/cailen> 
# 
# Inspired by the EC2 inventory plugin:
# https://github.com/ansible/ansible/blob/devel/contrib/inventory/ec2.py
#
# This file is part of Ansible,
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

######################################################################

import os
import sys
import re
import argparse
from time import time
import ConfigParser
import ast
try:
    import json
except ImportError:
    import simplejson as json

try:
    from anetpy.manager import AnetManager
except ImportError as importerror:
    sys.exit("failed=True msg='`anetpy` library required for this script'")



class AtlanticNetInventory(object):

    ###########################################################################
    # Main execution path
    ###########################################################################

    def __init__(self):
        ''' Main execution path '''

        # Atlantic.Net Inventory data
        self.data = {}      # All Atlantic.Net data
        self.inventory = {} # Ansible Inventory

        # Define defaults
        self.cache_path = '.'
        self.cache_max_age = 0
        self.group_variables = {}

        # Read settings, environment variables, and CLI arguments
        self.read_settings()
        self.read_environment()
        self.read_cli_args()

        # Verify credentials were set
        if not hasattr(self, 'public_key') or not hasattr(self, 'private_key'):
            sys.stderr.write('''Could not find values for Atlantic.Net public_key and private_key.
They must be specified via either ini file, command line argument (--public_key and --private_key),
or environment variables (ANET_PUBLIC_KEY and ANET_PRIVATE_KEY)\n''')
            sys.exit(-1)

        # env command, show Atlantic.Net credentials
        if self.args.env:
            print "ANET_PUBLIC_KEY=%s" % self.public_key
            print "ANET_PRIVATE_KEY=%s" % self.private_key
            sys.exit(0)

        # Manage cache
        self.cache_filename = self.cache_path + "/ansible-atlantic_net.cache"
        self.cache_refreshed = False

        if self.is_cache_valid:
            self.load_from_cache()
            if len(self.data) == 0:
                if self.args.force_cache:
                    sys.stderr.write('''Cache is empty and --force-cache was specified\n''')
                    sys.exit(-1)

        self.manager = AnetManager(self.public_key, self.private_key, "2010-12-30")

        # Pick the json_data to print based on the CLI command
        if self.args.cloudservers:
            self.load_from_atlantic_net('cloudservers')
            json_data = {'cloudservers': self.data['cloudservers']}
        elif self.args.images:
            self.load_from_atlantic_net('images')
            json_data = {'images': self.data['images']}
        elif self.args.plans:
            self.load_from_atlantic_net('plans')
            json_data = {'plans': self.data['plans']}
        elif self.args.ssh_keys:
            self.load_from_atlantic_net('ssh_keys')
            json_data = {'ssh_keys': self.data['ssh_keys']}
        elif self.args.all:
            self.load_from_atlantic_net()
            json_data = self.data
        elif self.args.host:
            json_data = self.load_cloudserver_variables_for_host()
        else:    # '--list' this is last to make it default
            self.load_from_atlantic_net('cloudservers')

            self.build_inventory()
            json_data = self.inventory

        if self.cache_refreshed:
            self.write_to_cache()

        if self.args.pretty:
            print json.dumps(json_data, sort_keys=True, indent=2)
        else:
            print json.dumps(json_data)
        # That's all she wrote...


    ###########################################################################
    # Script configuration
    ###########################################################################

    def read_settings(self):
        ''' Reads the settings from the anet_inventory.ini file '''
        config = ConfigParser.SafeConfigParser()
        config.read(os.path.dirname(os.path.realpath(__file__)) + '/anet_inventory.ini')

        # Credentials
        if config.has_option('atlantic_net', 'public_key'):
            self.public_key = config.get('atlantic_net', 'public_key')

        if config.has_option('atlantic_net', 'private_key'):
            self.private_key = config.get('atlantic_net', 'private_key')

        # Cache related
        if config.has_option('atlantic_net', 'cache_path'):
            self.cache_path = config.get('atlantic_net', 'cache_path')
        if config.has_option('atlantic_net', 'cache_max_age'):
            self.cache_max_age = config.getint('atlantic_net', 'cache_max_age')

        # Group variables
        if config.has_option('atlantic_net', 'group_variables'):
            self.group_variables = ast.literal_eval(config.get('atlantic_net', 'group_variables'))

    def read_environment(self):
        ''' Reads the settings from environment variables '''
        # Setup credentials
        if os.getenv("ANET_PUBLIC_KEY"):
            self.api_token = os.getenv("ANET_PUBLIC_KEY")
        if os.getenv("ANET_PRIVATE_KEY"):
            self.api_token = os.getenv("ANET_PRIVATE_KEY")

    def read_cli_args(self):
        ''' Command line argument processing '''
        parser = argparse.ArgumentParser(description='Produce an Ansible Inventory file based on Atlantic.Net credentials')

        parser.add_argument('--list', action='store_true', help='List all active Cloudservers as Ansible inventory (default: True)')
        parser.add_argument('--host', action='store', help='Get all Ansible inventory variables about a specific Cloudserver')

        parser.add_argument('--all', action='store_true', help='List all Atlantic.Net information as JSON')
        parser.add_argument('--cloudservers', '-c', action='store_true', help='List Cloudservers as JSON')
        parser.add_argument('--images', action='store_true', help='List Images as JSON')
        parser.add_argument('--plans', action='store_true', help='List Plans as JSON')
        parser.add_argument('--ssh-keys', action='store_true', help='List SSH keys as JSON')

        parser.add_argument('--pretty', '-p', action='store_true', help='Pretty-print results')

        parser.add_argument('--cache-path', action='store', help='Path to the cache files (default: .)')
        parser.add_argument('--cache-max_age', action='store', help='Maximum age of the cached items (default: 0)')
        parser.add_argument('--force-cache', action='store_true', default=False, help='Only use data from the cache')
        parser.add_argument('--refresh-cache', '-r', action='store_true', default=False,
                            help='Force refresh of cache by making API requests to Atlantic.Net (default: False - use cache files)')

        parser.add_argument('--env', '-e', action='store_true', help='Display ANET_PUBLIC_KEY and ANET_PRIVATE_KEY')
        parser.add_argument('--public_key', '-a', action='store', help='Atlantic.Net Public Key')
        parser.add_argument('--private_key', '-b', action='store', help='Atlantic.Net Private Key')

        self.args = parser.parse_args()

        if self.args.public_key:
            self.public_key = self.args.public_key

        if self.args.private_key:
            self.private_key = self.args.private_key

        # Make --list default if none of the other commands are specified
        if (not self.args.cloudservers and not self.args.images and 
                not self.args.plans and not self.args.ssh_keys and
                not self.args.all and not self.args.host):
            self.args.list = True


    ###########################################################################
    # Data Management
    ###########################################################################

    def load_from_atlantic_net(self, resource=None):
        '''Get JSON from Atlantic.Net API'''
        if self.args.force_cache:
            return
        # We always get fresh cloudservers
        if self.is_cache_valid() and not (resource == 'cloudservers' or resource is None):
            return
        if self.args.refresh_cache:
            resource = None

        if resource == 'cloudservers' or resource is None:
            self.data['cloudservers'] = self.manager.all_active_cloudservers()
            self.cache_refreshed = True
        if resource == 'images' or resource is None:
            self.data['images'] = self.manager.all_images(filter=None)
            self.cache_refreshed = True
        if resource == 'plans' or resource is None:
            self.data['plans'] = self.manager.plans()
            self.cache_refreshed = True
        if resource == 'ssh_keys' or resource is None:
            self.data['ssh_keys'] = self.manager.all_ssh_keys()
            self.cache_refreshed = True

    def build_inventory(self):
        '''Build Ansible inventory of cloudservers'''
        self.inventory = {
                            'all': {
                                    'hosts':[],
                                    'vars': self.group_variables
                                   },
                            '_meta': {'hostvars':{}}
                        }

        # add all cloudservers by id and name
        for cloudserver in self.data['cloudservers']:
            dest = cloudserver['vm_ip_address']

            self.inventory['all']['hosts'].append(dest)

            self.inventory[cloudserver['id']] = [dest]
            self.inventory[cloudserver['name']] = [dest]

            # groups that are always present
            for group in [
                            'image_' + self.to_safe(cloudserver['vm_image']),
                            'plan_' + cloudserver['vm_plan_name'],
                            'distro_' + self.to_safe(cloudserver['vm_image_display_name']),
                            'status_' + cloudserver['vm_status']
                        ]:
                if group not in self.inventory:
                    self.inventory[group] = {'hosts':[], 'vars': {}}
                self.inventory[group]['hosts'].append(dest)

            # groups that are not always present
            for group in [
                            cloudserver['vm_image'],
                            cloudserver['vm_image_display_name']
                         ]:
                if group:
                    image = 'image_' + self.to_safe(group)
                    if image not in self.inventory:
                        self.inventory[image] = {'hosts':[], 'vars': {}}
                    self.inventory[image]['hosts'].append(dest)

    def load_cloudserver_variables_for_host(self):
        '''Generate a JSON response to a --host call'''
        host = int(self.args.host)

        cloudserver = self.manager.show_cloudserver(host)

        # Put all the information in a 'anet_' namespace
        info = {}
        for k, v in cloudserver.items():
            info['anet_'+k] = v

        return {'cloudserver': info}



    ###########################################################################
    # Cache Management
    ###########################################################################

    def is_cache_valid(self):
        ''' Determines if the cache files have expired, or if it is still valid '''
        if os.path.isfile(self.cache_filename):
            mod_time = os.path.getmtime(self.cache_filename)
            current_time = time()
            if (mod_time + self.cache_max_age) > current_time:
                return True
        return False


    def load_from_cache(self):
        ''' Reads the data from the cache file and assigns it to member variables as Python Objects'''
        try:
            cache = open(self.cache_filename, 'r')
            json_data = cache.read()
            cache.close()
            data = json.loads(json_data)
        except IOError:
            data = {'data': {}, 'inventory': {}}

        self.data = data['data']
        self.inventory = data['inventory']


    def write_to_cache(self):
        ''' Writes data in JSON format to a file '''
        data = {'data': self.data, 'inventory': self.inventory}
        json_data = json.dumps(data, sort_keys=True, indent=2)

        cache = open(self.cache_filename, 'w')
        cache.write(json_data)
        cache.close()


    ###########################################################################
    # Utilities
    ###########################################################################

    def push(self, my_dict, key, element):
        ''' Pushed an element onto an array that may not have been defined in the dict '''
        if key in my_dict:
            my_dict[key].append(element)
        else:
            my_dict[key] = [element]


    def to_safe(self, word):
        ''' Converts 'bad' characters in a string to underscores so they can be used as Ansible groups '''
        return re.sub("[^A-Za-z0-9\-\.]", "_", word)



###########################################################################
# Run the script
AtlanticNetInventory()
