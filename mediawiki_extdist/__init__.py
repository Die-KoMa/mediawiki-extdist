#!/usr/bin/env python3
"""
nightly.py - tarball creator

This is a script that creates tarballs
for MediaWiki extensions based on the
configuration in conf.py. It accepts
some optional arguments:

* --all: Generate tarballs for all extensions.
* --skins: Process skins instead of extensions
* --force: Regenerate all tarballs even if they already exist

By default, it generates only the tarball for the
VisualEditor extension (or the Vector skin if
--skins is passed). This will change in the future
when debugging becomes less rare.
"""

import glob
import json
import logging
import os
import random
import requests
import subprocess
import sys
import shutil
import traceback
import argparse
import tempfile


class TarballGenerator(object):
    def __init__(self, conf, repo_type='extensions', force=False):
        self.API_URL = conf['API_URL']
        self.DIST_PATH = conf['DIST_PATH']
        self.GIT_URL = conf['GIT_URL']
        self.LOG_FILE = conf['LOG_FILE']
        self.SRC_PATH = conf['SRC_PATH']
        self.PID_FILE = conf['PID_FILE']
        self.LOG_FILE = conf['LOG_FILE']
        self.REPO_TYPE = repo_type
        self.EXT_PATH = os.path.join(self.SRC_PATH, self.REPO_TYPE)
        self.COMPOSER = conf.get('COMPOSER')
        self._repo_list = None
        self._extension_config = None
        self.force = force
        self.session = requests.Session()

    @property
    def repo_list(self):
        """
        Lazy-load the list of all extensions
        """
        if self._repo_list is None:
            self._repo_list = self.fetch_all_repos()
        return self._repo_list

    def fetch_all_repos(self):
        """
        Does an API request to get the complete list of extensions.
        Do not call directly.
        """
        logging.debug('Fetching list of all %s...' % self.REPO_TYPE)
        data = {
            'action': 'query',
            'list': 'extdistrepos',
            'format': 'json'
        }
        r = self.session.get(self.API_URL, params=data)
        r.raise_for_status()
        return r.json()['query']['extdistrepos'][self.REPO_TYPE]

    @property
    def supported_versions(self):
        """
        Lazy-load the list of supported branches
        """
        if self._extension_config is None:
            self.fetch_extension_config()
        return self._extension_config['snapshots']

    def fetch_extension_config(self):
        """
        Fetch the ExtensionDistributor configuration from the API
        Do not call this directly.
        """
        logging.debug('Fetching ExtensionDistributor config from API...')
        data = {
            'action': 'query',
            'meta': 'siteinfo',
            'format': 'json',
        }
        r = self.session.get(self.API_URL, params=data)
        r.raise_for_status()
        resp = r.json()
        self._extension_config = resp['query']['general']['extensiondistributor']

        return {
            'versions': resp['query']['general']['extensiondistributor']['snapshots'],
            'extension-list': resp['query']['general']['extensiondistributor']['list']
        }

    def init(self):
        """
        Does basic initialization
        """
        # Set up logging
        logging.basicConfig(
            filename=self.LOG_FILE,
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)s:%(message)s'
        )

        # Check to make sure nightly.py isn't already running
        if os.path.exists(self.PID_FILE):
            with open(self.PID_FILE, 'r') as f:
                old_pid = f.read()

            if self.check_pid(int(old_pid)):
                logging.warning('Another process of nightly.py is still running, quitting this one')
                quit()

        self.create_pid_file()

        # Init some directories we'll need
        if not os.path.isdir(self.EXT_PATH):
            self.shell_exec(['mkdir', '-p', self.EXT_PATH])
        if not os.path.isdir(self.DIST_PATH):
            self.shell_exec(['mkdir', '-p', self.DIST_PATH])

    def shell_exec(self, args, **kwargs):
        """
        Shortcut wrapper to execute a shell command

        >>> self.shell_exec(['ls', '-l'])
        """
        return subprocess.check_output(args, **kwargs).decode()

    def update_extension(self, ext, versions=None):
        """
        Fetch an extension's updates, and
        create new tarballs if needed
        """
        full_path = os.path.join(self.EXT_PATH, ext)
        logging.info('Starting update for %s' % ext)
        repo_url = self.GIT_URL % ext
        if not os.path.exists(full_path):
            os.chdir(self.EXT_PATH)
            logging.debug('Cloning %s' % ext)
            self.shell_exec(['git', 'clone', repo_url, ext])
            pass
        for branch in (versions or self.supported_versions):
            os.chdir(full_path)
            logging.info('Creating %s for %s' % (branch, ext))
            # In case GIT_URL has changed
            self.shell_exec(['git', 'remote', 'set-url', 'origin', repo_url])
            # Update remotes
            self.shell_exec(['git', 'fetch'])
            try:
                # Could fail if repo is empty
                self.shell_exec(['git', 'reset', '--hard', 'origin/master'])
                # Reset everything!
                self.shell_exec(['git', 'clean', '-ffdx'])
                # Checkout the branch
                self.shell_exec(['git', 'checkout', 'origin/%s' % branch])
            except subprocess.CalledProcessError:
                # Just a warning because this is expected for some extensions
                logging.warning('could not checkout origin/%s' % branch)
                continue
            # Reset everything, again.
            self.shell_exec(['git', 'clean', '-ffd'])
            # Sync submodules in case their urls have changed
            self.shell_exec(['git', 'submodule', 'sync'])
            # Update them, initializing new ones if needed
            self.shell_exec(['git', 'submodule', 'update', '--init'])
            # Gets short hash of HEAD
            rev = self.shell_exec(['git', 'rev-parse', '--short=7', 'HEAD']).strip()
            # filename rev must be exactly 7 characters to match MW extension. (T365416)
            tarball_fname = '%s-%s.tar.gz' % (ext, branch)
            if not self.force and os.path.exists(os.path.join(self.DIST_PATH, tarball_fname)):
                logging.debug('No updates to branch, tarball already exists.')
                continue
            if self.COMPOSER and os.path.exists('composer.json'):
                with open('composer.json') as f_composer:
                    d_composer = json.load(f_composer)
                if 'require' in d_composer:
                    logging.debug('Running composer install for %s' % ext)
                    try:
                        self.shell_exec([self.COMPOSER, 'install', '--no-dev', '--ignore-platform-reqs'])
                    except subprocess.CalledProcessError:
                        logging.error(traceback.format_exc())
                        logging.error('composer install failed')
            # Create gitinfo.json to be read/displayed by Special:Version
            git_info = {}
            with open('.git/HEAD') as f_head:
                head = f_head.read()
            if head.startswith('ref:'):
                head = head[5:]  # Strip 'ref :'
            git_info['head'] = head
            # Get the SHA-1
            git_info['headSHA1'] = self.shell_exec(['git', 'rev-parse', 'HEAD'])
            git_info['headCommitDate'] = self.shell_exec(['git', 'show', '-s', '--format=format:%ct', 'HEAD'])
            if head.startswith('refs/heads'):
                gi_branch = head.split('/')[-1]
            else:
                gi_branch = head
            git_info['branch'] = gi_branch
            git_info['remoteURL'] = self.GIT_URL % ext
            with open('gitinfo.json', 'w') as f:
                json.dump(git_info, f)

            # TODO: Stop writing this file now that we have gitinfo.json
            # Create a 'version' file with basic info about the tarball
            with open('version', 'w') as f:
                f.write('%s: %s\n' % (ext, branch))
                f.write(self.shell_exec(['date', '+%Y-%m-%dT%H:%M:%S']) + '\n')  # TODO: Do this in python
                f.write(rev + '\n')
            old_tarballs = glob.glob(os.path.join(self.DIST_PATH, '%s-%s-*.tar.gz' % (ext, branch)))
            logging.debug('Deleting old tarballs...')
            for old in old_tarballs:
                # FIXME: Race condition, we should probably do this later on...
                os.unlink(old)
            os.chdir(self.EXT_PATH)
            # Finally, create the new tarball
            self.shell_exec(['tar', '--exclude', '.git', '-czhPf', tarball_fname, ext])
        logging.debug('Moving new tarballs into dist/')
        tarballs = glob.glob(os.path.join(self.EXT_PATH, '*.tar.gz'))
        for tar in tarballs:
            fname = tar.split('/')[-1]
            shutil.move(tar, os.path.join(self.DIST_PATH, fname))
        logging.info('Finished update for %s' % ext)

        if random.randint(0, 99) == 0:
            # Run git gc every 100th process (statistically)
            self.shell_exec(['git', 'gc'], cwd=full_path)

    def check_pid(self, pid):
        """
        Checks whether the given pid is running
        """
        try:
            # This doesn't actually kill it, just checks if it is running
            os.kill(pid, 0)
        except OSError:
            # Not running
            return False
        else:
            # So it must be running
            return True

    def create_pid_file(self):
        """
        Creates a pid file with the current pid
        """
        with open(self.PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        logging.info('Creating pid file')

    def run(self, repos=None, versions=None):
        self.init()
        if not repos:
            repos = self.repo_list
        logging.info('Processing %s %s' % (len(repos), self.REPO_TYPE))
        logging.info('Starting update of all %s...' % self.REPO_TYPE)
        for repo in repos:
            try:
                self.update_extension(repo, versions=versions)
            except KeyboardInterrupt:
                logging.error(traceback.format_exc())
                sys.exit(1)
            except Exception:
                logging.error(traceback.format_exc())
                logging.error('Updating %s failed, skipping' % repo)
        logging.info('Finished update of all %s!' % self.REPO_TYPE)


def main():
    with tempfile.TemporaryDirectory() as src_path:
      # Load our config from JSON
      conf = {
          "API_URL" : "https://www.mediawiki.org/w/api.php",
          "GIT_URL" : "https://gerrit.wikimedia.org/r/mediawiki/extensions/%s",
          "DIST_PATH" : None,
          "SRC_PATH" : src_path,
          "PID_FILE" : "extdist.pid",
          "LOG_FILE" : "extdist.log",
          "VERSIONS" : None,
      }

      parser = argparse.ArgumentParser(prog="mediawiki-extdist",
                                       description="packages MediaWiki extension tarballs")
      parser.add_argument('--force', action='store_true')
      parser.add_argument('--extension', action='append')
      parser.add_argument('--mw-version', action='append')
      parser.add_argument('--output', required=True)

      arguments = parser.parse_args()
      conf["DIST_PATH"] = arguments.output

      repo_type = 'extensions'
      generator = TarballGenerator(conf, repo_type=repo_type, force=arguments.force)
      generator.run(repos=arguments.extension, versions=arguments.mw_version)


if __name__ == '__main__':
    main()
