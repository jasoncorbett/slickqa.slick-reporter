#!/usr/bin/env python3.3
"""
Slick Reporter is a simple script that can run a command, examine it's output, and report results
to slick based on output.  It uses regular expressions configured in a configuration file to define
how and what it pays attention to.
"""
__author__ = 'jcorbett'

import re
import sys
import configparser
import slickqa
import logging
import logging.handlers
import argparse
import subprocess
import time

################################################################################
# Logging
################################################################################
class StrFormatLogRecord(logging.LogRecord):
    """
    Even though you can select '{' as the style for the formatter class, you still can't use
    {} formatting for your message.  This is stupid, so this class will fix it.
    """

    def getMessage(self):
        msg = str(self.msg)
        if self.args:
            # potential bug here, if they provide a 0th argument, but don't use it in the message.
            # the old formatting would have thrown an exception in that case, and it still will.
            if '{}' in msg or '{0}' in msg:
                msg = msg.format(*self.args)
            else:
                msg = msg % self.args
        return msg

# Allow {} style formatting of log messages, which is far superior!
logging.setLogRecordFactory(StrFormatLogRecord)

def initialize_logging(configuration):
    assert(isinstance(configuration, configparser.ConfigParser))
    logfile = configuration['Logging'].get('logfile')
    level = configuration['Logging'].get('level', 'DEBUG')
    stdout = configuration['Logging'].getboolean('stdout', True)
    format = configuration['Logging'].get('format', '[{asctime}|{levelname:<8}|{name}]: {message}')
    dateformat = configuration['Logging'].get('dateformat', '%x %I:%M:%S %p')
    handlers = []
    formatter = logging.Formatter(fmt=format, datefmt=dateformat, style='{')
    root_logger = logging.getLogger()
    exc_info = None
    try:
        if logfile is not None and logfile != '':
            handlers.append(logging.handlers.WatchedFileHandler(logfile))
            if stdout:
                handlers.append(logging.StreamHandler())
        else:
            # if there is no logfile, you have to have stdout logging
            handlers.append(logging.StreamHandler())
    except PermissionError:
        handlers = [logging.StreamHandler(),]
        exc_info = sys.exc_info()
    root_logger.setLevel(level)
    if root_logger.hasHandlers():
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
    for handler in handlers:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    if exc_info is not None:
        logger = logging.getLogger("slick-reporter.initialize_logging")
        logger.warning("Unable to write to log file {}: ", logfile, exc_info=exc_info)

################################################################################
# Configuration
################################################################################

basic_configuration = """
[Slick]
url = http://localhost:8080
project = Another Project
component = Search
release = 6
testplan = Search Query Automation
build.command = echo 1.0.0-7
build.regex = .*-(?P<build>\d+)

[Test]
command = cat example-output.txt
output.regex = \[(?P<result>.*?)\](?:\[(?P<reason>.*?)\])? \| (?P<name>.*?) \| (?P<counts>.*?) \| ElapsedMS: (?P<runlength>\d+)
name = Search {name}
reason = {reason}: {counts}

[Logging]
logfile = slick-reporter.log
level = INFO
stdout = True
format = [{asctime}|{levelname:<8}|{name}]: {message}
dateformat = %x %I:%M:%S %p
"""

def load_configuration(filepath):
    logger = logging.getLogger("slick-reporter.load_configuration")
    logger.debug("Loading configuration from file path {}", filepath)
    config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
    config.read_string(basic_configuration)
    files_read = config.read([filepath,])
    if len(files_read) is 0:
        logger.debug("Unable to read config file '{}', using defaults.", filepath)
    return config

def save_configuration(filepath, config):
    assert(isinstance(config, configparser.ConfigParser))
    logger = logging.getLogger("slick-reporter.save_configuration")
    logger.debug("Attempting to save configuration file {}.", filepath)
    try:
        with open(filepath, 'w') as configfile:
            config.write(configfile)
    except:
        logger.warn("Unable to write configuration file '{}': ", filepath, exc_info=sys.exc_info())
        raise


################################################################################
# Slick Initialization
################################################################################

class Slick(object):

    def __init__(self, slickcon, configuration):
        assert isinstance(slickcon, slickqa.SlickConnection)
        assert isinstance(configuration, configparser.ConfigParser)
        self.slickcon = slickcon
        self.config = configuration

        self.logger = logging.getLogger('slick-reporter.Slick')
        self.logger.debug("Initializing Slick Reporting...")
        self.project = None
        self.component = None
        self.componentref = None
        self.testplan = None
        self.releaseref = None
        self.release = None
        self.buildref = None
        self.testrun = None
        self.testrunref = None

        self.init_project()
        self.init_release()
        self.init_build()
        self.init_component()
        self.init_testplan()
        self.init_testrun()

    def init_project(self):
        self.logger.debug("Looking for project by name '{}'.", self.config['Slick']['project'])
        exc_info = None
        try:
            self.project = self.slickcon.projects.findByName(self.config['Slick']['project'])
        except slickqa.SlickCommunicationError as err:
            self.logger.error("Error communicating with slick: {}", err.args[0])
        if self.project is None:
            self.logger.error("Unable to find project with name '{}'", self.config['Slick']['project'])
            raise slickqa.SlickCommunicationError("Unable to find project with name '{}' on slick located at '{}'".format(self.config['Slick']['project'], self.slickcon.baseUrl))
        assert(isinstance(self.project, slickqa.Project))
        self.logger.info("Found project with name '{}' and id: {}.", self.project.name, self.project.id)

    def init_release(self):
        release_name = self.config['Slick']['release']
        self.logger.debug("Looking for release '{}' in project '{}'", release_name, self.project.name)
        for release in self.project.releases:
            assert isinstance(release, slickqa.Release)
            if release.name == release_name:
                self.logger.info("Found Release '{}' with id '{}' in Project '{}'.", release.name, release.id, self.project.id)
                self.release = release
                self.releaseref = release.create_reference()
                break
        else:
            self.logger.info("Adding release {} to project {}.", release_name, self.project.name)
            release = slickqa.Release()
            release.name = release_name
            self.release = self.slickcon.projects(self.project).releases(release).create()
            assert isinstance(self.release, slickqa.Release)
            self.project.releases.append(self.release)
            self.releaseref = self.release.create_reference()
            self.logger.info("Using newly created release '{}' with id '{}' in Project '{}'.", self.release.name, self.release.id, self.project.name)

    def init_build(self):
        build_number = None
        command = None
        regex = None
        if 'build' in self.config['Slick']:
            build_number = self.config['Slick']['build']
        if 'build.command' in self.config['Slick']:
            command = self.config['Slick']['build.command']
        if 'build.regex' in self.config['Slick']:
            try:
                regex = re.compile(self.config['Slick'].get('build.regex', raw=True), re.MULTILINE)
            except re.error as err:
                raise slickqa.SlickCommunicationError("Regular expression <<{}>> is not valid: {}", self.config['Slick']['build.regex'], err.args[0])

        if (build_number is None and command is None) or (build_number is not None and command is not None and regex is None):
            raise slickqa.SlickCommunicationError("You must either supply a build in the configuration, or use the build.command and build.regex.")
        if command is not None and regex is not None:
            try:
                self.logger.debug("Running command <<{}>> and examining output for build number.", command)
                output = subprocess.check_output(command, shell=True)
                matches = regex.search(str(output))
                if matches:
                    self.logger.debug("Found a match to regular expression, looking for 'build' in match dictionary.")
                    match_dict = matches.groupdict()
                    if 'build' in match_dict:
                        build_number = matches.groupdict()['build']
                        self.logger.debug("Found build name '{}' in output from command.", build_number)
                    else:
                        self.logger.error("Unable to find 'build' key in match dictionary.  Did you include a (?P<build>) group in your regular expression?  You need to in order to get the build number from a command output")
            except subprocess.CalledProcessError as err:
                self.logger.warn("Command <<{}>> had an invalid return code {}, output: {}", command, err.returncode, err.output)
        if build_number is None:
            raise slickqa.SlickCommunicationError("No build number was specified.  You can specify it in the Slick section of the configuration file using either build key, or build.command and build.regex keys.")
        self.logger.debug("Looking for an existing build {} in project {} and release {}.", build_number, self.project.name, self.releaseref.name)

        for build in self.release.builds:
            if build.name == build_number:
                self.logger.debug("Found build with name '{}' and id '{}' on release '{}'", build.name, build.id, self.release.name)
                self.buildref = build.create_reference()
                break
        else:
            self.logger.info("Adding build {} to release {}.", build_number, self.release.name)
            build = slickqa.Build()
            build.name = build_number
            self.buildref = (self.slickcon.projects(self.project).releases(self.release).builds(build).create()).create_reference()
            assert isinstance(self.buildref, slickqa.BuildReference)
            self.logger.info("Using newly created build '{}' with id '{}' in Release '{}' in Project '{}'.", self.buildref.name, self.buildref.buildId, self.release.name, self.project.name)

    def init_component(self):
        if 'component' in self.config['Slick']:
            comp_name = self.config['Slick']['component']
            self.logger.debug("Looking for component with name '{}' in project '{}'", comp_name, self.project.name)
            for comp in self.project.components:
                if comp.name == comp_name:
                    assert isinstance(comp, slickqa.Component)
                    self.logger.info("Found component with name '{}' and id '{}' in project '{}'.", comp.name, comp.id, self.project.name)
                    self.component = comp
                    self.componentref = self.component.create_reference()
                    assert isinstance(self.componentref, slickqa.ComponentReference)
                    break
            else:
                self.logger.info("Adding component {} to project {}.", comp_name, self.project.name)
                component = slickqa.Component()
                component.name = comp_name
                self.component = self.slickcon.projects(self.project).components(component).create()
                self.componentref = self.component.create_reference()
                self.logger.info("Using newly created component '{}' with id '{}' in project '{}'.", self.component.name, self.component.id, self.project.name)
        else:
            self.logger.warn("No component specified in the config under the Slick section.  Tests will not have an associated component.")

    def init_testplan(self):
        if 'testplan' in self.config['Slick']:
            testplan_name = self.config['Slick']['testplan']
            testplan = self.slickcon.testplans.findOne(projectid=self.project.id, name=testplan_name)
            if testplan is None:
                self.logger.debug("Creating testplan with name '{}' connected to project '{}'.", testplan_name, self.project.name)
                testplan = slickqa.Testplan()
                testplan.name = testplan_name
                testplan.project = self.project.create_reference()
                testplan.isprivate = False
                testplan.createdBy = "slick-reporter"
                testplan = self.slickcon.testplans(testplan).create()
                self.logger.info("Using newly create testplan '{}' with id '{}'.", testplan.name, testplan.id)
            else:
                self.logger.info("Found (and using) existing testplan '{}' with id '{}'.", testplan.name, testplan.id)
            self.testplan = testplan
        else:
            self.logger.warn("No testplan specified for the testrun.")

    def init_testrun(self):
        testrun = slickqa.Testrun()
        testrun.name = 'Tests run from slick-reporter'
        if self.testplan is not None:
            testrun.name = 'Testrun for testplan {}'.format(self.testplan.name)
            testrun.testplanid = self.testplan.id
        testrun.project = self.project.create_reference()
        testrun.release = self.releaseref
        testrun.build = self.buildref
        testrun.state = slickqa.RunStatus.RUNNING
        testrun.runStarted = int(round(time.time() * 1000))

        self.logger.debug("Creating testrun with name {}.", testrun.name)
        self.testrun = self.slickcon.testruns(testrun).create()

    def finish_testrun(self):
        assert isinstance(self.testrun, slickqa.Testrun)
        testrun = slickqa.Testrun()
        testrun.id = self.testrun.id
        testrun.runFinished = int(round(time.time() * 1000))
        testrun.state = slickqa.RunStatus.FINISHED
        self.slickcon.testruns(testrun).update()

    def file_result(self, name, status=slickqa.ResultStatus.PASS, reason=None, runlength=0):
        test = self.slickcon.testcases.findOne(projectid=self.project.id, name=name)
        if test is None:
            self.logger.debug("Creating testcase with name '{}' on project '{}'.", name, self.project.name)
            test = slickqa.Testcase()
            test.name = name
            test.project = self.project.create_reference()
            test = self.slickcon.testcases(test).create()
            self.logger.info("Using newly created testcase with name '{}' and id '{}' for result.", name, test.id)
        else:
            self.logger.info("Found testcase with name '{}' and id '{}' for result.", test.name, test.id)
        result = slickqa.Result()
        result.testrun = self.testrun.create_reference()
        result.testcase = test.create_reference()
        result.project = self.project.create_reference()
        result.release = self.releaseref
        result.build = self.buildref
        if self.component is not None:
            result.component = self.componentref
        result.reason = reason
        result.runlength = runlength
        result.end = int(round(time.time() * 1000))
        result.started = result.end - result.runlength
        result.status = status
        self.logger.debug("Filing result of '{}' for test with name '{}'", result.status, result.testcase.name)
        result = self.slickcon.results(result).create()
        self.logger.info("Filed result of '{}' for test '{}', result id: {}", result.status, result.testcase.name, result.id)




################################################################################
# Tester
################################################################################
class ConfigurationError(Exception):

    def __init__(self, *args, **kwargs):
        super(ConfigurationError).__init__(*args, **kwargs)

class CommandTester(object):

    def __init__(self, configuration, slick):
        assert isinstance(configuration, configparser.ConfigParser)
        assert isinstance(slick, Slick)
        self.config = configuration
        self.slick = slick
        logger =logging.getLogger("slickreporter.CommandTester.__init__")
        logger.debug("Looking to make sure we have all the necessary configuration options.")
        all_accounted_for = True
        required_config = ['command', 'output.regex']
        for required in required_config:
            if required not in self.config['Test']:
                logger.error("Missing configuration in [Test] section: {}", required)
                all_accounted_for = False
        if not all_accounted_for:
            raise ConfigurationError("Several required configurations were missing.")
        logger.debug("Found all required configurations {}.", required_config)
        self.command = self.config['Test']['command']
        logger.debug("Compiling regular expression.")
        self.output_regex = re.compile(self.config['Test'].get('output.regex', raw=True))
        logger.debug("Regular expression <<{}>> compiled successfully.", self.output_regex.pattern)

    def run_command(self):
        logger = logging.getLogger("slickreporter.CommandTester.run_command")
        logger.info("Running command <<{}>> and examaning it's output.", self.config['Test']['command'])
        process = subprocess.Popen(self.command, shell=True, stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
        begin = int(round(time.time() * 1000))
        for line in process.stdout:
            line = line.decode('utf-8').strip()
            logger.debug("Testing output from command <<{}>> against regular expression <<{}>>.", line, self.output_regex.pattern)
            match = self.output_regex.match(line)
            if match is not None:
                end = int(round(time.time() * 1000))
                groups = match.groups()
                groupdict = match.groupdict()
                logger.debug("Matched output, groups={}, groupdict={}", groups, groupdict)

                result = slickqa.ResultStatus.BROKEN_TEST
                if 'result' in self.config['Test']:
                    result = self.config['Test'].get('result', raw=True).format(*groups, **groupdict)
                elif 'result' in groupdict:
                    result = groupdict['result']

                reason = ""
                if 'reason' in groupdict and groupdict['reason'] is None:
                    groupdict['reason'] = "Output Indicated {}".format(result)
                if 'reason' in self.config['Test']:
                    reason = self.config['Test'].get('reason', raw=True).format(*groups, **groupdict)
                elif 'reason' in groupdict:
                    reason = groupdict['reason']

                name = "Command {}".format(self.command)
                if 'name' in self.config['Test']:
                    name = self.config['Test'].get('name', raw=True).format(*groups, **groupdict)
                elif 'name' in groupdict:
                    name = groupdict['name']

                runlength = end - begin
                begin = end
                if 'runlength' in self.config['Test']:
                    try:
                        runlength = int(self.config['Test'].get('runlength', raw=True).format(*groups, **groupdict))
                    except BaseException as err:
                        logger.warn("Error converting runlength from config into integer: ", err.args[0])
                elif 'runlength' in groupdict:
                    try:
                        runlength = int(groupdict['runlength'])
                    except BaseException as err:
                        logger.warn("Error converting runlength from match into integer: ", err.args[0])

                self.slick.file_result(name=name, status=result, reason=reason, runlength=runlength)




################################################################################
# Main
################################################################################
def setup(options):
    """This method gets everything ready.  It loads configuration, initializes logging, initializes the connection
    to slick.
    """
    config = load_configuration(options.configpath)
    if options.stdout:
        config['Logging']['stdout'] = 'True'
    if hasattr(options, 'nologfile') and options.nologfile:
        config['Logging']['logfile'] = ''
    if hasattr(options, 'loglevel') and options.loglevel is not None and options.loglevel != '':
        config['Logging']['level'] = options.loglevel
    if hasattr(options, 'slickurl') and options.slickurl is not None and options.slickurl != '':
        config['Slick']['url'] = options.slickurl
    if hasattr(options, 'logfile') and options.logfile is not None and options.logfile != '':
        config['Logging']['logfile'] = options.logfile
    initialize_logging(config)
    logger = logging.getLogger("slick-reporter.setup")
    logger.info("slick-reporter is initializing.")
    logger.debug("Configuring a slick connection at url '{}'.", config['Slick']['url'])
    slick = slickqa.SlickConnection(config['Slick']['url'])
    validate_slick_connection(slick)

    return (config, slick)

def validate_slick_connection(slick):
    assert(isinstance(slick, slickqa.SlickConnection))
    logger = logging.getLogger("slick-reporter.validate_slick_connection")
    logger.debug("Attempting to connect to slick at url {}", slick.baseUrl)
    versioninfo = slick.version.findOne()
    logger.info("Connected to {} version {} at url {}", versioninfo.productName, versioninfo.versionString, slick.baseUrl)

def main(args=sys.argv[1:]):
    """
    The main method of the script, it does everything.

    :type args: list
    :param args: A list of strings that are the command line arguments to the script.  The default is sys.argv[1:]
    """
    parser = argparse.ArgumentParser(description="slick-reporter is used to take the output from a command line script and turn it into slick results.")
    parser.add_argument('-c', '--config', action='store', default='slick-reporter.conf', dest='configpath', metavar='CONFIGPATH', help="specify the config file slick-reporter uses, default=slick-reporter.conf")
    parser.add_argument('-n', '--nologfile', action='store_true', default=False, dest='nologfile', help="don't send logs to the slick-reporter.log log file")
    parser.add_argument('--loglevel', action='store', default=None, dest='loglevel', help="Change the default log level from INFO to another level (like DEBUG, WARNING, ERROR, or CRITICAL)")
    parser.add_argument('-q', '--quiet', action='store_false', default=True, dest='stdout', help="don't log to stdout")
    parser.add_argument('--slick', action='store', dest='slickurl', metavar='SLICKBASEURL', help='Use the specified url for connecting to slick')
    parser.add_argument('--configure', action='store_true', dest='configure', default=False, help="Configure slick-reporter")

    options = parser.parse_args(args)

    (config, slick) = setup(options)
    log = logging.getLogger('slick-reporter.main')
    if options.configure:
        save_configuration(options.configpath, config)
        print("Configuration saved to {}.".format(options.configpath))
        sys.exit(0)
    reporter = None
    try:
        reporter = Slick(slick, config)
        tester = CommandTester(config, reporter)
        tester.run_command()
    except slickqa.SlickCommunicationError as err:
        logging.fatal("Unable to initialize slick because: {}", err.args[0])
        sys.exit(1)
    except ConfigurationError as err:
        logging.fatal("Unable to run test due to configuration issue: {}", err.args[0])
        sys.exit(1)
    except re.error as err:
        logging.fatal("Invalid regular expression: {}", err[0], exc_info=sys.exc_info())
        sys.exit(1)
    finally:
        if reporter is not None:
            reporter.finish_testrun()


if __name__ == '__main__':
    main()
