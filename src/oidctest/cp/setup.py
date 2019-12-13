import importlib
import logging
import os
import shelve
import sys

from oic import rndstr
from oic.utils.authn.authn_context import AuthnBroker
from oic.utils.authn.client import verify_client
from oic.utils.authz import AuthzHandling
from oic.utils.clientdb import BaseClientDatabase
from oic.utils.keyio import keyjar_init
from oic.utils.sdb import create_session_db
from oic.utils.userinfo import UserInfo

from oidctest.endpoints import ENDPOINTS
from oidctest.endpoints import add_endpoints
from oidctest.rp.provider import Provider

LOGGER = logging.getLogger(__name__)

__author__ = 'roland'


class InMemoryBCD(BaseClientDatabase):
    def __init__(self):
        self.db = {}

    def __getitem__(self, item):
        return self.db[item]

    def __setitem__(self, key, value):
        self.db[key] = value

    def __delitem__(self, key):
        del self.db[key]

    def keys(self):
        return self.db.keys()

    def items(self):
        return self.db.items()


def main_setup(args, lookup=None):
    sys.path.insert(0, ".")
    config = importlib.import_module(args.config)
    if args.path:
        if config.baseurl.endswith('/'):
            config.issuer = '{}{}/'.format(config.baseurl, args.path)
        else:
            config.issuer = '{}/{}/'.format(config.baseurl, args.path)
    elif args.port and args.port not in [80, 443]:
        if config.baseurl.endswith('/'):
            config.issuer = '{}:{}/'.format(config.baseurl[:-1], args.port)
        else:
            config.issuer = '{}:{}/'.format(config.baseurl, args.port)

    _baseurl = config.issuer

    if not _baseurl.endswith("/"):
        _baseurl += "/"

    com_args = {
        "name": config.issuer,
        "baseurl": _baseurl,
        "client_authn": verify_client,
        "symkey": config.SYM_KEY,
        "template_lookup": lookup,
        "template": {"form_post": "form_response.mako"},
        "jwks_name": "./static/jwks_{}.json"
    }

    # Client data base
    try:
        com_args['cdb'] = InMemoryBCD()
        #com_args['cdb'] = shelve.open(config.CLIENT_DB, writeback=True)
    except AttributeError:
        pass

    try:
        _auth = config.AUTHENTICATION
    except AttributeError:
        pass
    else:
        ab = AuthnBroker()

        for authkey, value in list(_auth.items()):
            authn = None

            if "NoAuthn" == authkey:
                from oic.utils.authn.user import NoAuthn

                authn = NoAuthn(None, user=_auth[authkey]["user"])

            if authn is not None:
                ab.add(_auth[authkey]["ACR"], authn, _auth[authkey]["WEIGHT"])

        com_args['authn_broker'] = ab

        # dealing with authorization
        com_args['authz'] = AuthzHandling()

    try:
        if config.USERINFO == "SIMPLE":
            # User info is a simple dictionary in this case statically defined in
            # the configuration file
            com_args['userinfo'] = UserInfo(config.USERDB)
        else:
            com_args['userinfo'] = None
    except AttributeError:
        pass

    # Should I care about verifying the certificates used by other entities
    if args.insecure:
        com_args["verify_ssl"] = False
    else:
        com_args["verify_ssl"] = True

    try:
        assert os.path.isfile(config.SERVER_CERT)
        assert os.path.isfile(config.SERVER_KEY)
        com_args['client_cert'] = (config.SERVER_CERT, config.SERVER_KEY)
    except AttributeError:
        pass
    except AssertionError:
        print("Can't access client certificate and/or client secret")
        exit(-1)

    op_arg = {}

    try:
        op_arg["cookie_ttl"] = config.COOKIETTL
    except AttributeError:
        pass

    try:
        op_arg["cookie_name"] = config.COOKIENAME
    except AttributeError:
        pass


    # print URLS
    if args.debug:
        op_arg["debug"] = True

    # All endpoints the OpenID Connect Provider should answer on
    add_endpoints(ENDPOINTS)
    op_arg["endpoints"] = ENDPOINTS

    op_arg["baseurl"] = _baseurl

    # Add own keys for signing/encrypting JWTs
    try:
        # a throw-away OP used to do the initial key setup
        _sdb = create_session_db(com_args["baseurl"], 'automover', '430X', {})
        _op = Provider(sdb=_sdb, **com_args)
        jwks = keyjar_init(_op, config.keys)
    except KeyError:
        raise
        pass
    else:
        op_arg["jwks"] = jwks
        op_arg['keyjar'] = _op.keyjar
        #op_arg["keys"] = config.keys

    try:
        op_arg["marg"] = multi_keys(com_args, config.multi_keys)
    except AttributeError as err:
        pass

    return com_args, op_arg, config


def cb_setup(args, lookup=None):
    sys.path.insert(0, ".")
    config = importlib.import_module(args.config)
    if args.path:
        if config.baseurl.endswith('/'):
            config.issuer = '{}{}/'.format(config.baseurl, args.path)
        else:
            config.issuer = '{}/{}/'.format(config.baseurl, args.path)
    elif args.port:
        if args.port in [80, 443]:
            if config.baseurl.endswith('/'):
                config.issuer = config.baseurl
            else:
                config.issuer = '{}/'.format(config.baseurl)
        else:
            if config.baseurl.endswith('/'):
                config.issuer = '{}:{}/'.format(config.baseurl[:-1], args.port)
            else:
                config.issuer = '{}:{}/'.format(config.baseurl, args.port)

    _baseurl = config.issuer

    if not _baseurl.endswith("/"):
        _baseurl += "/"

    com_args = {
        "name": config.issuer,
        "baseurl": _baseurl,
        "client_authn": verify_client,
        "template_lookup": lookup,
        "template": {"form_post": "form_response.mako"},
        "jwks_name": "./static/jwks_{}.json"
    }

    try:
        com_args["symkey"] = config.SYM_KEY
    except AttributeError:
        pass

    try:
        com_args["seed"] = config.SEED
    except AttributeError:
        com_args['seed'] = rndstr().encode("utf-8")

    try:
        com_args['sso_ttl'] = config.SSO_TTL
    except AttributeError:
        pass

    # Client data base
    try:
        com_args['cdb'] = InMemoryBCD()
        #com_args['cdb'] = shelve.open(config.CLIENT_DB, writeback=True)
    except AttributeError:
        pass

    try:
        _auth = config.AUTHENTICATION
    except AttributeError:
        pass
    else:
        ab = AuthnBroker()

        for authkey, value in list(_auth.items()):
            authn = None

            if "NoAuthn" == authkey:
                from oic.utils.authn.user import NoAuthn

                authn = NoAuthn(None, user=_auth[authkey]["user"])

            if authn is not None:
                ab.add(_auth[authkey]["ACR"], authn, _auth[authkey]["WEIGHT"])

        com_args['authn_broker'] = ab

        # dealing with authorization
        com_args['authz'] = AuthzHandling()

    try:
        if config.USERINFO == "SIMPLE":
            # User info is a simple dictionary in this case statically defined in
            # the configuration file
            com_args['userinfo'] = UserInfo(config.USERDB)
        else:
            com_args['userinfo'] = None
    except AttributeError:
        pass

    # Should I care about verifying the certificates used by other entities
    if args.insecure:
        com_args["verify_ssl"] = False
    else:
        com_args["verify_ssl"] = True

    try:
        assert os.path.isfile(config.SERVER_CERT)
        assert os.path.isfile(config.SERVER_KEY)
        com_args['client_cert'] = (config.SERVER_CERT, config.SERVER_KEY)
    except AttributeError:
        pass
    except AssertionError:
        print("Can't access client certificate and/or client secret")
        exit(-1)

    try:
        com_args['logout_path'] = config.LOGOUT_PATH
    except AttributeError:
        pass

    op_arg = {}

    for key, val in config.COOKIE.items():
        op_arg["cookie_{}".format(key)] = val

    # print URLS
    if args.debug:
        op_arg["debug"] = True

    # All endpoints the OpenID Connect Provider should answer on
    add_endpoints(ENDPOINTS)
    op_arg["endpoints"] = ENDPOINTS

    op_arg["baseurl"] = _baseurl

    # Add own keys for signing/encrypting JWTs
    try:
        # a throw-away OP used to do the initial key setup
        _sdb = create_session_db(com_args["baseurl"], 'automover', '430X', {})
        _op = Provider(sdb=_sdb, **com_args)
        jwks = keyjar_init(_op, config.keys)
        # Add keys under the issuer ID
        _bj = _op.keyjar.export_jwks(True, '')
        _op.keyjar.import_jwks(_bj, _op.baseurl)
    except KeyError:
        pass
    else:
        op_arg["jwks"] = jwks
        op_arg['keyjar'] = _op.keyjar
        # op_arg["keys"] = config.keys

    try:
        op_arg["marg"] = multi_keys(com_args, config.multi_keys)
    except AttributeError as err:
        pass

    return com_args, op_arg, config


def multi_keys(com_args, key_conf):
    # a throw-away OP used to do the initial key setup
    _sdb = create_session_db(com_args["baseurl"], 'automover', '430X', {})
    _op = Provider(sdb=_sdb, **com_args)
    jwks = keyjar_init(_op, key_conf, "m%d")

    return {"jwks": jwks, "keys": key_conf}
