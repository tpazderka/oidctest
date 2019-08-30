#!/usr/bin/env python3
import importlib
import logging
import os
import sys
import traceback
from urllib.parse import quote_plus

import cherrypy
#from oic.oic import Client
from oic.oic.message import OIDCMessageFactory
from otest.aus.client import Factory
from otest.aus.handling_ph import WebIh
from otest.conf_setup import construct_app_args
from otest.utils import SERVER_LOG_FOLDER
from otest.utils import setup_logging

from oidctest.cp import dump_log
from oidctest.cp.log_handler import OPLog
from oidctest.cp.log_handler import OPTar
from oidctest.op import check
from oidctest.op import func
from oidctest.op import oper
from oidctest.op import profiles
from oidctest.op.client import Client
from oidctest.op.profiles import PROFILEMAP
from oidctest.optt import Main
from oidctest.prof_util import ProfileHandler
from oidctest.session import SessionHandler
from oidctest.tool import WebTester
from oidctest.tt.rest import REST
from oidctest.file_system import FileSystem

logger = logging.getLogger("")
LOGFILE_NAME = 'op_test.log'
hdlr = logging.FileHandler(LOGFILE_NAME)
base_formatter = logging.Formatter(
    "%(asctime)s %(name)s:%(levelname)s %(message)s")

hdlr.setFormatter(base_formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)


def pick_grp(name):
    return name.split('-')[1]


def get_version():
    sys.path.insert(0, ".")
    vers = importlib.import_module('version')
    return vers.VERSION


def make_webenv(config, rest):
    if args.tag:
        qtag = quote_plus(args.tag)
    else:
        qtag = 'default'

    ent_conf = None
    try:
        ent_conf = rest.construct_config(quote_plus(args.issuer), qtag)
    except Exception as err:
        print('iss:{}, tag:{}'.format(quote_plus(args.issuer), qtag))
        for m in traceback.format_exception(*sys.exc_info()):
            print(m)
        exit()

    setup_logging("%s/rp_%s.log" % (SERVER_LOG_FOLDER, args.port), logger)
    logger.info('construct_app_args')

    _path, app_args = construct_app_args(args, config, oper, func, profiles,
                                         ent_conf)

    # Application arguments
    app_args.update(
        {"msg_factory": OIDCMessageFactory, 'profile_map': PROFILEMAP,
         'profile_handler': ProfileHandler,
         'client_factory': Factory(Client)})

    if args.insecure:
        app_args['client_info']['verify_ssl'] = False

    return app_args


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-k', dest='insecure', action='store_true',
        help="insecure mode for when you're expecting to talk HTTPS to "
             "servers that use self-signed certificates")
    parser.add_argument(
        '-i', dest='issuer',
        help="The issuer ID of the OP")
    parser.add_argument(
        '-f', dest='flowdir',
        help="A directory that contains the flow definitions for all the tests")
    parser.add_argument('-p', dest='port', type=int,
                        help="Which port the server should listen on")
    parser.add_argument('-P', dest='path', type=int,
                        help="path added to the base URL")
    # parser.add_argument('-P', dest='profile')
    parser.add_argument('-H', dest='htmldir',
                        help="Root directory for the HTML template files")
    parser.add_argument('-S', dest='staticdir', default='static',
                        help="Directory where static files are kept")
    parser.add_argument('-s', dest='tls', action='store_true',
                        help="Whether the server should support incoming HTTPS")
    parser.add_argument(
        '-t', dest='tag',
        help="An identifier used to distinguish between different "
             "configuration for the same OP instance")
    parser.add_argument(
        '-m', dest='path2port',
        help="CSV file containing the path-to-port mapping that the reverse "
             "proxy (if used) is using")

    parser.add_argument(dest="config")
    args = parser.parse_args()

    _vers = get_version()

    cherrypy.tools.dumplog = cherrypy.Tool('before_finalize', dump_log)

    cherrypy.config.update(
        {'environment': 'production',
         'log.error_file': 'site.log',
         'tools.trailing_slash.on': False,
         'log.screen': True,
         'tools.sessions.on': True,
         'tools.encode.on': True,
         'tools.encode.encoding': 'utf-8',
         'tools.dumplog.on': True,
         'server.socket_host': '0.0.0.0',  # listen on all interfaces
         'server.socket_port': args.port
         })

    folder = os.path.abspath(os.curdir)

    if args.staticdir.startswith('/'):
        _static = args.staticdir
    else:
        _static = os.path.join(folder, args.staticdir)

    provider_config = {
        '/': {
            'root_path': 'localhost',
            'tools.staticdir.root': folder,
        },
        '/static': {
            'tools.staticdir.dir': 'static',
            'tools.staticdir.debug': True,
            'tools.staticdir.on': True,
        },
        '/jwks': {
            'tools.staticdir.dir': 'jwks',
            'tools.staticdir.debug': True,
            'tools.staticdir.on': True,
        },
        '/export': {
            'tools.staticdir.dir': 'export',
            'tools.staticdir.debug': True,
            'tools.staticdir.on': True,
        },
        '/requests': {
            'tools.staticdir.dir': 'requests',
            'tools.staticdir.debug': True,
            'tools.staticdir.on': True,
        },
        '/favicon.ico':
        {
            'tools.staticfile.on': True,
            'tools.staticfile.filename': os.path.join(folder, 'static/favicon.ico')
        },
        '/robots.txt':
        {
            'tools.staticfile.on': True,
            'tools.staticfile.filename': os.path.join(folder, 'static/robots.txt')
        }
    }

    _conf = importlib.import_module(args.config)
    if args.htmldir:
        _html = FileSystem(args.htmldir)
    else:
        _html = FileSystem(_conf.PRE_HTML)
    _html.sync()

    rest = REST('')
    webenv = make_webenv(_conf, rest)

    session_handler = SessionHandler(args.issuer, args.tag,
                                     flows=webenv['flow_state'], rest=rest,
                                     version=_vers, **webenv)
    session_handler.iss = args.issuer
    session_handler.tag = args.tag
    info = WebIh(session=session_handler, pre_html=_html, **webenv)
    tester = WebTester(info, session_handler, flows=webenv['flow_state'],
                       check_factory=check.factory, **webenv)

    log_root = os.path.join(folder, 'log')
    _tar = OPTar(folder)
    cherrypy.tree.mount(_tar, '/mktar')
    cherrypy.tree.mount(_tar, '/backup')
    cherrypy.tree.mount(OPLog(log_root, _html, version=_vers,
                              iss=args.issuer, tag=args.tag), '/log')

    cherrypy.tree.mount(
        Main(tester, webenv['flow_state'], webenv, pick_grp), '/',
        provider_config)

    # If HTTPS
    if args.tls:
        cherrypy.config.update({'cherrypy.server.ssl_module': 'builtin'})
        cherrypy.server.ssl_certificate = _conf.SERVER_CERT
        cherrypy.server.ssl_private_key = _conf.SERVER_KEY
        if _conf.CA_BUNDLE:
            cherrypy.server.ssl_certificate_chain = _conf.CA_BUNDLE

    cherrypy.engine.start()
    cherrypy.engine.block()
