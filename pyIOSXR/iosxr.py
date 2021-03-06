# Copyright 2015 Netflix. All rights reserved.
#
# The contents of this file are licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the
# License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

import re
import sys
import difflib
import pexpect
from exceptions import XMLCLIError, InvalidInputError

import xml.etree.ElementTree as ET


# Build and execute xml requests.
def __execute_rpc__(device, rpc_command):
    rpc_command = '<?xml version="1.0" encoding="UTF-8"?><Request MajorVersion="1" MinorVersion="0">'+rpc_command+'</Request>'
    device.sendline(rpc_command)
    device.expect("<.*</Response>")
    response = device.match.group()

    root = ET.fromstring(response)
    childs = [x.tag for x in list(root)]

    if int(root.find('ResultSummary').get('ErrorCount')) > 0:

        if 'CLI' in childs:
            error_msg = root.find('CLI').get('ErrorMsg')
        else:
            error_msg = root.get('ErrorMsg')

        error_msg += '\nOriginal call was: %s' % rpc_command
        raise XMLCLIError(error_msg)

    if 'CLI' in childs:
        cli_childs = [x.tag for x in list(root.find('CLI'))]
        if 'Configuration' in cli_childs:
            output = root.find('CLI').find('Configuration').text
            if output is None:
                output = ''
            elif 'Invalid input detected' in output:
                raise InvalidInputError('Invalid input entered:\n%s' % output)

    return root

# Ecexute show commands not in config context.
def __execute_show__(device, show_command):
    rpc_command = '<CLI><Exec>'+show_command+'</Exec></CLI>'
    response = __execute_rpc__(device, rpc_command)
    return response.find('CLI').find('Exec').text.lstrip()

# Ecexute show commands not in config context.
def __execute_config_show__(device, show_command):
    rpc_command = '<CLI><Configuration>'+show_command+'</Configuration></CLI>'
    response = __execute_rpc__(device, rpc_command)
    return response.find('CLI').find('Configuration').text.lstrip()


class IOSXR:

    def __init__(self, hostname, username, password):
        """
        A device running IOS-XR.

        :param hostname:  IP or FQDN of the device you want to connect to
        :param username:  Username
        :param password:  Password
        """
        self.hostname = hostname
        self.username = username
        self.password = password

    def __getattr__(self, item):
        """
        Ok, David came up with this kind of dynamic method. It takes
        calls with show commands encoded in the name. I'll replacs the
        underscores for spaces and issues the show command... pretty neat!
        """
        def wrapper(*args, **kwargs):
            cmd = item.replace('_', ' ')
            response = __execute_show__(self.device, cmd)
            match = re.search(".*(!! IOS XR Configuration.*)</Exec>",response,re.DOTALL)
            if match is not None:
                response = match.group(1)
            return response

        if item.startswith('show'):
            return wrapper
        else:
            raise AttributeError("type object '%s' has no attribute '%s'" % (self.__class__.__name__, item))

    def open(self):
        """
        Opens a connection to an IOS-XR device.
        """
        device = pexpect.spawn('ssh ' + self.username + '@' + self.hostname)
        index = device.expect(['\(yes\/no\)\?', 'password:', pexpect.EOF])
        if index == 0:
          device.sendline('yes')
          index = device.expect(['\(yes\/no\)\?', 'password:', pexpect.EOF])
        if index == 1:
          device.sendline(self.password)
        elif index == 2:
          pass
        device.expect('#')
        device.sendline('xml')
        device.expect('XML>')
        self.device = device
        rpc_command = '<Lock/>'
        response = __execute_rpc__(self.device, rpc_command)

    def close(self):
        """
        Closes the connection to the IOS-XR device.
        """
        rpc_command = '<Unlock/>'
        response = __execute_rpc__(self.device, rpc_command)
        self.device.close()

    def load_candidate_config(self, filename=None, config=None):
        """
        Populates the attribute candidate_config with the desired
        configuration and loads it into the router. You can populate it from
        a file or from a string. If you send both a filename and a string
        containing the configuration, the file takes precedence.

        :param filename:  Path to the file containing the desired
                          configuration. By default is None.
        :param config:    String containing the desired configuration.
        """
        configuration = ''

        if filename is None:
            configuration = config
        else:
            with open(filename) as f:
                configuration = f.read()

        rpc_command = '<CLI><Configuration>'+configuration+'</Configuration></CLI>'

        try:
            __execute_rpc__(self.device, rpc_command)
        except InvalidInputError as e:
            self.discard_config()
            raise InvalidInputError(e.message)

    def compare_config(self):
        """
        Compares executed candidate config with the running config and
        returns a diff, assuming the loaded config will be merged with the
        existing one.

        :return:  Config diff.
        """
        show_merge = __execute_config_show__(self.device, 'show configuration merge')
        show_run = __execute_show__(self.device, 'show running-config')

        diff = difflib.unified_diff(show_run.splitlines(1)[2:-2],show_merge.splitlines(1)[2:-2],n=0)
        diff = ''.join([x.replace('\r', '') for x in diff])
        return diff

    def compare_replace_config(self):
        """
        Compares executed candidate config with the running config and
        returns a diff, assuming the entire config will be replaced.

        :return:  Config diff.
        """
        diff = __execute_config_show__(self.device, 'show configuration changes diff')

        return ''.join(diff.splitlines(1)[2:-2])

    def commit_config(self):
        """
        Commits the candidate config to the device, by merging it with the
        existing one.
        """
        rpc_command = '<Commit/>'
        response = __execute_rpc__(self.device, rpc_command)

    def commit_replace_config(self):
        """
        Commits the candidate config to the device, by replacing the existing
        one.
        """
        rpc_command = '<Commit Replace="true"/>'
        response = __execute_rpc__(self.device, rpc_command)

    def discard_config(self):
        """
        Clears uncommited changes in the current session.
        """
        rpc_command = '<Clear/>'
        response = __execute_rpc__(self.device, rpc_command)

    def rollback(self):
        """
        Used after a commit, the configuration will be reverted to the
        previous committed state.
        """
        rpc_command = '<Unlock/><Rollback><Previous>1</Previous></Rollback><Lock/>'
        response = __execute_rpc__(self.device, rpc_command)

