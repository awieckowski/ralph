#!/usr/bin/env python
# -*- coding: utf-8 -*-

import atexit
import errno
import fcntl
import logging
import optparse
import os
import re
import subprocess
import sys
import urllib2

from urllib import urlencode


def all(iterable):
    """The built-in was unavailable no Python 2.4."""
    for element in iterable:
        if not element:
            return False
    return True


class SimpleDHCPManager(object):
    def __init__(
        self, api_url, api_username, api_key, mode, dhcp_config, restart,
        logger, env, **kwargs
    ):
        self.api_url = api_url.rstrip('/')
        self.api_username = api_username
        self.api_key = api_key
        self.mode = mode.upper()
        self.dhcp_config_path = dhcp_config
        self.dhcp_service_name = restart
        self.logger = logger
        self.env = env

    def update_configuration(self):
        config = self._get_configuration()
        if self._configuration_is_valid(config):
            if self._set_new_configuration(config):
                return self._send_confirm()
        return False

    def _get_configuration(self):
        url = "{}/dhcp-config{}/?{}".format(
            self.api_url,
            '-head' if self.mode == 'NETWORKS' else '',
            urlencode({
                'username': self.api_username,
                'api_key': self.api_key,
            })
        )
        if self.env:
            url += '&env=' + self.env
        req = urllib2.Request(url)
        try:
            resp = urllib2.urlopen(req)
        except urllib2.URLError, e:
            self.logger.error(
                'Could not get configuration from Ralph. Error '
                'message: %s' % e,
            )
            return None
        data = resp.read()
        self.logger.info(
            'Read %d kilobytes of DHCP configuration.' % (len(data) / 1024),
        )
        return data

    def _configuration_is_valid(self, config):
        if not config:
            return False
        config = config.strip()
        start_str = '# DHCP'
        stop_str = '# End of autogenerated config'
        return config.startswith(start_str) and config.endswith(stop_str)

    def _restart_dhcp_server(self):
        if not self.dhcp_service_name:
            self.logger.info('No dhcpd service name provided to restart.')
            return True
        command = ['service', self.dhcp_service_name, 'restart']
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        proc.wait()
        restart_successful = proc.returncode == 0
        if restart_successful:
            self.logger.info(
                'Service %s successfully restarted.' % self.dhcp_service_name,
            )
        else:
            self.logger.error(
                'Failed to restart service %s.' % self.dhcp_service_name,
            )
        return restart_successful

    def _set_new_configuration(self, config):
        try:
            if self.dhcp_config_path:
                f = open(self.dhcp_config_path, 'w')
                try:
                    f.write(config)
                finally:
                    f.close()
                self.logger.info(
                    'Configuration written to %s' % self.dhcp_config_path,
                )
            else:
                sys.stdout.write(config)
                self.logger.info('Configuration written to stdout.')
            return self._restart_dhcp_server()
        except IOError, e:
            self.logger.error(
                'Could not write new DHCP configuration. Error '
                'message: %s' % e,
            )
            return False

    def _send_confirm(self):
        url = "%s/dhcp-synch/?username=%s&api_key=%s" % (
            self.api_url, self.api_username, self.api_key,
        )
        req = urllib2.Request(url)
        try:
            urllib2.urlopen(req)
        except urllib2.URLError, e:
            self.logger.error(
                'Could not send confirmation to Ralph. Error message: %s' % e,
            )
            return False
        self.logger.info('Confirmation sent to %s.' % self.api_url)
        return True

    def _get_time_from_config(self, config_part):
        reg = r'#.+at ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})'
        m = re.match(reg, config_part)
        if m:
            return m.group(1)


def _get_cmd_options():
    opts_parser = optparse.OptionParser(
        description='Update configuration in DHCP server.',
    )
    opts_parser.add_option('-a', '--api-url', help='Ralph instance address.')
    opts_parser.add_option('-u', '--api-username', help='Ralph API username.')
    opts_parser.add_option('-k', '--api-key', help='Ralph API key.')
    opts_parser.add_option(
        '-m',
        '--mode',
        type='choice',
        choices=['entries', 'networks'],
        help='Choose what part of config you want to upgrade.',
    )
    opts_parser.add_option(
        '-l',
        '--log-path',
        help='Path to log file. [Default: STDOUT]',
        default='STDOUT',
    )
    opts_parser.add_option(
        '-c',
        '--dhcp-config',
        help='Path to the DHCP configuration file.',
    )
    opts_parser.add_option(
        '-d',
        '--dc',
        help='Only get config for the specified data center.',
    )
    opts_parser.add_option(
        '-r',
        '--restart',
        help='Name of the service to restart.',
    )
    opts_parser.add_option(
        '-v',
        '--verbose',
        help='Increase verbosity.',
        action="store_true",
        default=False,
    )
    opts = opts_parser.parse_args()[0]
    result = vars(opts)
    result['logger'] = _setup_logging(opts.log_path, opts.verbose)
    return result


def _setup_logging(filename, verbose):
    log_size = 20  # MB
    logger = logging.getLogger("RalphDHCPAgent")
    if verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)
    if not filename or filename in ('-', 'STDOUT'):
        # display to the screen
        handler = logging.StreamHandler()
    else:
        handler = logging.handlers.RotatingFileHandler(
            filename, maxBytes=(log_size * (1 << 20)), backupCount=5)
    fmt = logging.Formatter("[%(asctime)-12s.%(msecs)03d] "
                            "%(levelname)-8s %(filename)s:%(lineno)d  "
                            "%(message)s", "%Y-%m-%d %H:%M:%S")
    handler.setFormatter(fmt)

    logger.addHandler(handler)
    return logger


if __name__ == "__main__":
    opts = _get_cmd_options()
    require = ['api_url', 'api_username', 'api_key', 'mode']
    if not all(opts[k] for k in require):
        sys.stderr.write(
            'ERROR: %s '
            'are required.\n' % ', '.join(['--%s' % opt for opt in require]),
        )
        sys.exit(2)
    lockfile = '/tmp/%s.lock' % os.path.split(sys.argv[0])[1]
    f = open(lockfile, 'w')
    try:
        fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write('%d' % os.getpid())
        f.flush()
        atexit.register(os.unlink, lockfile)
    except IOError, e:
        if e.errno == errno.EAGAIN:
            opts['logger'].critical('Script already running.')
            sys.exit(2)
        raise
    sdm = SimpleDHCPManager(**opts)
    if not sdm.update_configuration():
        sys.exit(1)
