from future.backports.urllib.parse import parse_qs
from future.backports.urllib.parse import urlparse
from future.backports.urllib.parse import urlsplit

import inspect
import json
import logging
import sys

from jwkest.jwk import base64url_to_long
from jwkest.jwt import split_token
from oic.exception import MessageException
from oic.oauth2 import message
from oic.oauth2.message import ErrorResponse
from oic.oic.message import OIDCMessageFactory
# from oic.oic import claims_match
from oic.oic.message import SCOPE2CLAIMS
from oic.oic.message import AuthorizationRequest
from oic.oic.message import AuthorizationResponse
from oic.oic.message import IdToken
from oic.oic.message import OpenIDSchema
from oic.utils import time_util
from oic.utils.time_util import utc_time_sans_frac
from otest.aus.check import CONT_JSON
from otest.aus.check import CONT_JWT
from otest.check import CRITICAL
from otest.check import ERROR
from otest.check import INFORMATION
from otest.check import INTERACTION
from otest.check import OK
from otest.check import WARNING
from otest.check import Check
from otest.check import CriticalError
from otest.check import Error
from otest.check import Information
from otest.check import Other
from otest.check import ResponseInfo
from otest.check import Warnings
from otest.check import get_authorization_request
from otest.check import get_id_tokens
from otest.check import get_protocol_request
from otest.check import get_protocol_response
from otest.check import get_provider_info
from otest.events import EV_HTTP_ARGS
from otest.events import EV_HTTP_RESPONSE
from otest.events import EV_PROTOCOL_REQUEST
from otest.events import EV_PROTOCOL_RESPONSE
from otest.events import EV_REDIRECT_URL
from otest.events import EV_RESPONSE

from oidctest.regalg import MTI
from oidctest.regalg import REGISTERED_JWS_ALGORITHMS
from oidctest.regalg import REGISTERED_JWE_alg_ALGORITHMS
from oidctest.regalg import REGISTERED_JWE_enc_ALGORITHMS

__author__ = 'rohe0002'

logger = logging.getLogger(__name__)


def claims_match(value, claimspec):
    if claimspec is None:
        return True

    for key, val in claimspec.items():
        if key == "value":
            if value != val:
                return False
        elif key == "values":
            if value not in val:
                return False
                # Whether it's essential or not doesn't change anything here
    return True


class CmpIdtoken(Other):
    """
    Compares the JSON received as a CheckID response with my own
    interpretation of the ID Token.
    """
    cid = "compare-idtoken-received-with-check_id-response"

    def _func(self, conv):
        res = {}
        instance = get_protocol_response(conv, message.AuthorizationResponse)[0]

        kj = conv.entity.keyjar
        keys = {}
        for issuer in list(kj.keys()):
            keys.update(kj.get("ver", issuer=issuer))

        idt = IdToken().deserialize(instance["id_token"], "jwt", key=keys)
        if idt.to_dict() == conv.events.last_item(EV_RESPONSE).to_dict():
            pass
        else:
            self._status = self.status
            res["message"] = " ".join([
                "My deserialization of the IDToken differs from what the",
                "checkID response returned"])
        return res


class VerifyPromptNoneResponse(Check):
    """
    The OP may respond in more than one way and still be within
    what the spec says.
    Effect of prompt=none
    The Authorization Server MUST NOT display any authentication or
    consent user interface pages.
    """
    cid = "verify-prompt-none-response"
    msg = "OP error"

    def _func(self, conv):
        _response = conv.events.last_item(EV_HTTP_RESPONSE)
        _reply = conv.events.last_item(EV_RESPONSE)
        _client = conv.entity

        res = {}
        if _response.status_code == 400:
            err = ErrorResponse().deserialize(_reply, "json")
            err.verify()
            conv.events.store(EV_RESPONSE, err, sender=self.__class__.__name__)
            if err["error"] in ["consent_required", "interaction_required"]:
                # This is OK
                res["content"] = err.to_json()
            else:
                self._message = "Not an expected error"
                self._status = CRITICAL
        elif _response.status_code in [301, 302]:
            _loc = _response.headers["location"]
            callback = False
            for url in _client.redirect_uris:
                if _loc.startswith(url):
                    callback = True
                    break

            if not callback:
                self._message = "Not valid to not redirect back to RP"
                self._status = ERROR
                return res

            if "?" in _loc:
                _query = _loc.split("?")[1]
            elif "#" in _loc:
                _query = _loc.split("#")[1]
            else:  # ???
                self._message = "Expected info in the redirect"
                self._status = CRITICAL
                return res

            conv.events.store(EV_RESPONSE, _query,
                              sender=self.__class__.__name__)
            try:
                err = ErrorResponse().deserialize(_query, "urlencoded")
                err.verify()
                if err["error"] in ["consent_required", "interaction_required",
                                    "login_required"]:
                    # This is OK
                    res["content"] = err.to_json()
                    conv.events.store(EV_PROTOCOL_RESPONSE, err,
                                      sender=self.__class__.__name__)
                else:
                    self._message = "Not an expected error '%s'" % err[
                        "error"]
                    self._status = CRITICAL
            except MessageException:
                resp = AuthorizationResponse().deserialize(_query, "urlencoded")
                resp.verify()
                res["content"] = resp.to_json()
                conv.events.store(EV_PROTOCOL_RESPONSE, resp,
                                  sender=self.__class__.__name__)
        else:  # should not get anything else
            self._message = "Not an expected response"
            self._status = CRITICAL

        return res


class CheckSupported(CriticalError):
    """
    Checks that something asked for are supported
    """
    cid = "check-support"
    msg = "X not supported"
    element = "X_supported"
    parameter = "X"
    default = None
    required = False

    def _requested(self, request_args):
        return request_args[self.parameter]

    def _func(self, conv):
        res = {}
        try:
            _sup = self._supported(conv.req.request_args,
                                   get_provider_info(conv))
            if not _sup:
                self._status = self.status
                self._message = self.msg
        except KeyError:
            pass

        return res

    def _supported(self, request_args, provider_info):
        try:
            supported = provider_info[self.element]
        except KeyError:
            if self.default is None:
                if self.required:
                    return False
                else:
                    return True
            else:
                supported = self.default

        try:
            required = self._requested(request_args)
            if isinstance(required, str):
                if required not in supported:
                    return False
            else:
                for value in required:
                    if value not in supported:
                        return False
            return True
        except KeyError:
            pass

        return True


class CheckOPSupported(CheckSupported):
    pass


class CheckResponseType(CheckOPSupported):
    """
    Checks that the asked for response type are among the supported
    """
    cid = "check-response-type"
    msg = "Response type not supported"

    def _supported(self, request_args, provider_info):
        try:
            supported = [set(s.split(" ")) for s in
                         provider_info["response_types_supported"]]
        except KeyError:
            supported = [{"code"}]

        try:
            val = request_args["response_type"]
            if isinstance(val, str):
                rt = {val}
            else:
                rt = set(val)
            for sup in supported:
                if sup == rt:
                    return True
            return False
        except KeyError:
            pass

        return True


class VerifyIdTokenSigningAlgorithmIsSupported(Error):
    """
    Verify that required algorithms in id_token_signing_alg_values_supported
    """
    cid = "verify-id_token_signing-algorithm-is-supported"
    msg = "Verify ID token signing algorithm is in " \
          "id_token_signing_alg_values_supported"

    def _func(self, conv):
        _pi = get_provider_info(conv)

        for alg in self._kwargs['algs']:
            if alg not in _pi["id_token_signing_alg_values_supported"]:
                self._status = self.status
                self._message = "required algorithm %s is not in the list of " \
                                "supported algorithms" % alg
                break

        return {}


class CheckAcrSupport(CheckOPSupported):
    """
    Checks that the asked for acr are among the supported
    """
    cid = "check-acr-support"
    msg = "acr value not supported"

    def _supported(self, request_args, provider_info):
        try:
            supported = provider_info["acrs_supported"]
        except KeyError:
            return True

        try:
            # {"claims": {"acr": {"values": ["2"]}}}
            val = request_args["idtoken_claims"]
            acrs = val["claims"]["acr"]["values"]

            for acr in acrs:
                if acr in supported:
                    return True
            return False
        except KeyError:
            pass

        return True


class CheckScopeSupport(CheckOPSupported):
    """
    Checks that the asked for scope are among the supported
    """
    cid = "check-scope-support"
    msg = "Scope not supported"
    element = "scopes_supported"
    parameter = "scope"


class CheckUserIdSupport(CheckOPSupported):
    """
    Checks that the asked for acr are among the supported
    """
    cid = "check-userid-support"
    msg = "Subject type not supported"
    element = "subject_types_supported"
    parameter = "subject_type"


class CheckSignedUserInfoSupport(CheckSupported):
    """
    Checks that the asked for signature algorithms are among the supported
    """
    cid = "check-signed-userinfo-support"
    msg = "Signed UserInfo not supported"
    element = "userinfo_signing_alg_values_supported"
    parameter = "userinfo_signed_response_alg"


class CheckSignedIdTokenSupport(CheckSupported):
    """
    Checks that the asked for signature algorithms are among the supported
    """
    cid = "check-signed-idtoken-support"
    msg = "Signed ID Token algorithm not supported"
    element = "id_token_signing_alg_values_supported"
    parameter = "id_token_signed_response_alg"
    mti = False


class CheckSignedRequestObjectSupport(CheckSupported):
    """
    Checks that the asked for signature algorithms are among the supported
    """
    cid = "check-signed-request_object-support"
    msg = "Signed request object algorithm not supported"
    element = "request_object_signing_alg_values_supported"
    parameter = "request_object_signed_alg"
    mti = False


class CheckEncryptedUserInfoSupportALG(CheckSupported):
    """
    Checks that the asked for encryption algorithm are among the supported
    """
    cid = "check-signed-userinfo-alg-support"
    msg = "UserInfo encryption alg algorithm not supported"
    element = "userinfo_encryption_alg_values_supported"
    parameter = "userinfo_encrypted_response_alg"


class CheckEncryptedUserInfoSupportENC(CheckSupported):
    """
    Checks that the asked for encryption algorithm are among the supported
    """
    cid = "check-encrypt-userinfo-enc-support"
    msg = "UserInfo encryption enc algorithm not supported"
    element = "userinfo_encryption_enc_values_supported"
    parameter = "userinfo_encrypted_response_enc"


class CheckEncryptedIDTokenSupportALG(CheckSupported):
    """
    Checks that the asked for encryption algorithm are among the supported
    """
    cid = "check-encrypt-idtoken-alg-support"
    msg = "ID Token encryption alg algorithm not supported"
    element = "id_token_encryption_alg_values_supported"
    parameter = "id_token_encrypted_response_alg"


class CheckEncryptedIDTokenSupportENC(CheckSupported):
    """
    Checks that the asked for encryption algorithm are among the supported
    """
    cid = "check-encrypt-idtoken-enc-support"
    msg = "ID Token encryption enc method not supported"
    element = "id_token_encryption_enc_values_supported"
    parameter = "id_token_encrypted_response_enc"


class CheckEncryptedRequestObjectSupportALG(CheckSupported):
    """
    Checks that the asked for encryption algorithm are among the supported
    """
    cid = "check-encrypt-request_object-alg-support"
    msg = "Request_object encryption alg algorithm not supported"
    element = "request_object_encryption_alg_values_supported"
    parameter = "request_object_encryption_alg"


class CheckEncryptedRequestObjectSupportENC(CheckSupported):
    """
    Checks that the asked for encryption algorithm are among the supported
    """
    cid = "check-encrypt-idtoken-enc-support"
    msg = "Request_object encryption enc algorithm not supported"
    element = "request_object_encryption_enc_values_supported"
    parameter = "request_object_encryption_enc"


class CheckClaimsSupport(CheckOPSupported):
    """
    Checks that the asked for scope are among the supported
    """
    cid = "check-claims-support"
    msg = "Claims not supported"
    element = "claims_supported"
    parameter = "claims"

    def _requested(self, request_args):
        _req = request_args[self.parameter]
        return list(_req["userinfo"].keys())


# class CheckRequestClaimsSupport(CheckOPSupported):
#     """
#     Checks that the asked for scope are among the supported
#     """
#     cid = "check-request-claims-support"
#     msg = "Claims not supported"
#     element = "claims_supported"
#     parameter = "claims"
#
#     def _requested(self, request_args):
#         _req = CheckOPSupported._requested(self, request_args)
#         return _req["userinfo"].keys()
#

class CheckSupportedTrue(CriticalError):
    """
    Checks that a specific provider info parameter is supported
    """
    cid = "check-X-support"
    msg = "X not supported"
    element = "X"

    def _func(self, conv):
        res = {}

        try:
            val = get_provider_info(conv)[self.element]
        except KeyError:
            pass
        else:
            if val is True or val == "true":
                pass
            else:
                self._status = self.status
                self._message = self.msg

        return res


class CheckRequestParameterSupported(CheckSupportedTrue):
    """
    Checks that the request parameter is supported
    """
    cid = "check-request-parameter-supported-support"
    msg = "request parameter not supported"
    element = "request_parameter_supported"
    mti = False


class CheckRequestURIParameterSupported(CheckSupportedTrue):
    """
    Checks that the request parameter is supported
    """
    cid = "check-request_uri-parameter-supported-support"
    msg = "request_uri parameter not supported"
    element = "request_uri_parameter_supported"
    mti = False


class CheckIdTokenSignedResponseAlgSupport(CheckSupported):
    """
    Checks that the asked for id_token_signed_response_alg are among the
    supported
    """
    cid = "check-id_token_signed_response_alg-support"
    msg = "id_token_signed_response_alg not supported"
    element = "id_token_signed_response_alg_supported"
    parameter = "id_token_signed_response_alg"


class CheckTokenEndpointAuthMethod(CriticalError):
    """
    Checks that the token endpoint supports the used client authentication
    method
    """
    cid = "check-token-endpoint-auth-method"
    msg = "Client authentication method not supported"

    def _func(self, conv):
        try:
            _req = conv.request_spec
            if _req.request == "RegistrationRequest":
                _met = conv.request_args["token_endpoint_auth_method"]
            else:
                _met = conv.args["authn_method"]

            try:
                _pi = get_provider_info(conv)
                _sup = _pi["token_endpoint_auth_methods_supported"]
            except KeyError:
                _sup = None

            if not _sup:
                # MTI
                _sup = ["client_secret_basic"]

            if _met not in _sup:
                self._status = self.status
        except KeyError:
            pass

        return {}


class CheckContentTypeHeader(Error):
    """
    Verify that the content-type header is what it should be.
    """
    cid = "check_content_type_header"

    def _func(self, conv=None):
        res = {}
        _response = conv.events.last_item(EV_HTTP_RESPONSE)
        try:
            ctype = _response.headers["content-type"]
            if conv.response_spec.ctype == "json":
                if CONT_JSON in ctype or CONT_JWT in ctype:
                    pass
                else:
                    self._status = self.status
                    self._message = "Wrong content type: %s" % ctype
            else:  # has to be uuencoded
                if "application/x-www-form-urlencoded" not in ctype:
                    self._status = self.status
                    self._message = "Wrong content type: %s" % ctype
        except KeyError:
            pass

        return res


class CheckEndpoint(CriticalError):
    """ Checks that the necessary endpoint exists at a server """
    cid = "check-endpoint"
    msg = "Endpoint missing"

    def _func(self, conv=None):
        cls = conv.req.request
        try:
            endpoint = conv.entity.request2endpoint[cls]
        except KeyError:
            pass
        else:
            try:
                assert endpoint in get_provider_info(conv)
            except AssertionError:
                self._status = self.status
                self._message = "No '%s' endpoint provided" % endpoint

        return {}


class CheckHasJwksURI(Error):
    """
    Check that the jwks_uri discovery metadata value is in the provider_info
    """
    cid = "providerinfo-has-jwks_uri"
    msg = "jwks_uri discovery metadata value missing"

    def _func(self, conv):
        try:
            _ = get_provider_info(conv)["jwks_uri"]
        except KeyError:
            self._status = self.status
            self._message = "No 'jwks_uri' location provided"

        return {}


class CheckHasClaimsSupported(Error):
    """
    Check that the claims_supported discovery metadata value is in the
    provider_info
    """
    cid = "providerinfo-has-claims_supported"
    msg = "claims_supported discovery metadata value missing"

    def _func(self, conv):
        try:
            _ = get_provider_info(conv)["claims_supported"]
        except KeyError:
            self._status = self.status
            self._message = \
                "No 'claims_supported' discovery metadata value provided"

        return {}


class CheckProviderInfo(Error):
    """
    Check that the Provider Info is sound
    """
    cid = "check-provider-info"
    msg = "Provider information error"

    def _func(self, conv=None):
        # self._status = self.status
        return {}


class CheckRegistrationResponse(Error):
    """
    Verifies an Registration response. This is additional constrains besides
    what is optional or required.
    """
    cid = "check-registration-response"
    msg = "Registration response error"

    def _func(self, conv=None):
        # self._status = self.status
        return {}


class CheckAuthorizationResponse(Error):
    """
    Verifies an Authorization response. This is additional constrains besides
    what is optional or required.
    """
    cid = "check-authorization-response"

    def _func(self, conv=None):
        # self._status = self.status
        return {}


class LoginRequired(Error):
    """
    Verifies an Authorization error response. The error should be
    login_required.
    """
    cid = "login-required"

    def _func(self, conv=None):
        resp = conv.events.last_item(EV_PROTOCOL_RESPONSE)
        try:
            assert resp.type() == "AuthorizationErrorResponse"
        except AssertionError:
            self._status = self.status
            self._message = "Expected authorization error response, got %s" % (
                resp.type())

            try:
                assert resp.type() == "ErrorResponse"
            except AssertionError:
                self.status = CRITICAL
                self._message = "Expected an Error Response, got %s" % (
                    resp.type())
                return {}

        try:
            assert resp.error == "login_required"
        except AssertionError:
            self._status = self.status
            self._message = "Wrong error code"

        return {}


class InteractionNeeded(CriticalError):
    """
    A Web page was displayed for which no known interaction is defined.
    """
    cid = "interaction-needed"
    msg = "Unexpected page"

    def _func(self, conv=None):
        self._status = self.status
        self._message = None
        return {"url": conv.position}


class InteractionCheck(CriticalError):
    """
    A Web page was displayed for which no known interaction is defined.
    """
    cid = "interaction-check"

    def _func(self, conv=None):
        self._status = INTERACTION
        self._message = conv.events.last_item(EV_RESPONSE)
        parts = urlsplit(conv.position)
        return {"url": "{}://{}{}".format(parts[0], parts[1], parts[2])}


class VerifyClaims(Error):
    """
    Verifies that the claims returned as UserInfo or in the ID Token is
    consistent with what was asked for
    """
    cid = "verify-claims"
    msg = "Claims received do not match those requested"
    doc = """
    :param userinfo: Whether the method should look for the claims in the 
    user info
    :type userinfo: boolean
    :param id_token: Whether the method should look for the claims in the 
    id_token
    :type id_token: boolean
    
    Example:
        "verify-claims": {
          "id_token": null
        }
    """

    def _userinfo_claims(self, conv, req):
        userinfo_claims = {}
        try:
            _scopes = req["scope"]
        except KeyError:
            return {}

        for scope in _scopes:
            try:
                claims = dict([(name, None) for name in SCOPE2CLAIMS[scope]])
                userinfo_claims.update(claims)
            except KeyError:
                pass

        if "request" in req:
            jso = json.loads(split_token(req["request"])[1])
            _uic = jso["userinfo"]
            for key, val in list(_uic["claims"].items()):
                userinfo_claims[key] = val

        try:
            _userinfo_claims = req["claims"]["userinfo"]
        except KeyError:
            pass
        else:
            for key, val in list(_userinfo_claims.items()):
                userinfo_claims[key] = val

        logger.info('userinfo claims: {}'.format(userinfo_claims))

        missing = []
        extra = []
        mm = []
        # Get the UserInfoResponse, should only be one
        inst = get_protocol_response(conv, OpenIDSchema)[0]
        if userinfo_claims:
            for key, restr in list(userinfo_claims.items()):
                try:
                    if not claims_match(inst[key], restr):
                        mm.append(key)
                except KeyError:
                    missing.append(key)

        for key in list(inst.keys()):
            if key not in userinfo_claims:
                extra.append(key)

        msg = ""
        if missing:
            if len(missing) == 1:
                msg = "Missing required claim: %s" % missing[0]
            else:
                msg = "Missing required claims: %s" % missing
        if extra:
            if msg:
                msg += ", "
            if len(extra) == 1:
                msg += "Unexpected %s claim in response" % extra[0]
            else:
                msg += "Unexpected claims in response: %s" % extra

        if missing or extra or mm:
            self._message = msg
            self._status = WARNING
            return {
                "returned claims": list(inst.keys()),
                "expected claims": list(userinfo_claims.keys())}

        return {}

    def _idtoken_claims(self, conv, req, claims=None):
        try:
            if claims is None:
                claims = req["claims"]["id_token"]
        except KeyError:
            pass
        else:
            logger.info('IdToken claims: {}'.format(claims))
            res = get_id_tokens(conv)
            assert len(res)  # must be at least one
            _idt = res[0]

            mm = []
            missing = []
            for key, val in list(claims.items()):
                try:
                    if not claims_match(_idt[key], val):
                        mm.append(key)
                except KeyError:
                    missing.append(key)

            if missing or mm:
                msg = ""
                if missing:
                    if len(missing) == 1:
                        msg = "Missing required claim: %s" % missing[0]
                    else:
                        msg = "Missing required claims: %s" % missing
                if mm:
                    if msg:
                        msg += ", "
                    if len(mm) == 1:
                        msg += "Claim that didn't match request: %s" % mm[0]
                    else:
                        msg += "Claims that didn't match request: %s" % mm

                self._message = msg
                self._status = WARNING
                return {
                    "returned claims": list(_idt.keys()),
                    "required claims": list(claims.keys())}

        return {}

    def _func(self, conv=None):
        resp = {}
        req = get_authorization_request(conv, AuthorizationRequest)

        if "userinfo" in self._kwargs:
            if req['response_type'] == ['id_token']:
                claims = req["claims"]["userinfo"]
                ret = self._idtoken_claims(conv, req, claims)
                if ret:
                    resp["idtoken"] = ret
            else:
                ret = self._userinfo_claims(conv, req)
                if ret:
                    resp["userinfo"] = ret
        if "id_token" in self._kwargs:
            ret = self._idtoken_claims(conv, req)
            if ret:
                resp["idtoken"] = ret
        return resp


REQUIRED = {"essential": True}
OPTIONAL = None


class VerifyIDToken(CriticalError):
    """
    Verifies that the IDToken contains what it should
    """
    cid = "verify-id-token"
    msg = "IDToken error"

    def _func(self, conv):
        done = False

        idtoken_claims = {}
        req = get_authorization_request(conv, AuthorizationRequest)
        try:
            id_claims = req["claims"]["id_token"]
        except KeyError:
            pass
        else:
            idtoken_claims = id_claims.copy()

        for item in conv.events[EV_PROTOCOL_RESPONSE]:
            if self._status == self.status or done:
                break

            try:
                _idt = item["id_token"]
                if _idt is None:
                    continue
            except KeyError:
                continue

            idtoken = _idt
            for key, val in list(idtoken_claims.items()):
                if key == "max_age":
                    if idtoken["exp"] > (time_util.utc_time_sans_frac() + val):
                        self._status = self.status
                        diff = idtoken["exp"] - time_util.utc_time_sans_frac()
                        self._message = "exp too far in the future [%d]" % diff
                        break
                    else:
                        continue

                if val == OPTIONAL:
                    if key not in idtoken:
                        self._status = self.status
                        self._message = \
                            "'%s' claim was supposed to be present" % key
                        break
                elif val == REQUIRED:
                    try:
                        assert key in idtoken
                    except AssertionError:
                        self._status = self.status
                        self._message = \
                            "'%s' claim was expected to be present" % key
                        break
                elif "values" in val:
                    if key not in idtoken:
                        self._status = self.status
                        self._message = "Missing value on '%s' claim" % key
                        break
                    else:
                        _val = idtoken[key]
                        if isinstance(_val, str):
                            if _val not in val["values"]:
                                self._status = self.status
                                self._message = "Wrong value on '%s'" % key
                                break
                        elif isinstance(_val, int):
                            if _val not in val["values"]:
                                self._status = self.status
                                self._message = "Wrong value on '%s'" % key
                                break
                        else:
                            for sval in _val:
                                if sval in val["values"]:
                                    continue
                            self._status = self.status
                            self._message = "Wrong value on '%s'" % key
                            break

                done = True

        return {}


class RegistrationInfo(ResponseInfo):
    """Registration Response"""


class ProviderConfigurationInfo(ResponseInfo):
    """Provider Configuration Response"""


class UnpackAggregatedClaims(Error):
    cid = "unpack-aggregated-claims"

    def _func(self, conv=None):
        resp = conv.events.last_item(EV_PROTOCOL_RESPONSE)
        _client = conv.entity

        try:
            _client.unpack_aggregated_claims(resp)
        except Exception as err:
            self._message = "Unable to unpack aggregated claims: %s" % err
            self._status = self.status

        return {}


class ChangedSecret(Error):
    """
    Check whether the client secret has changed or not
    """
    cid = "changed-client-secret"

    def _func(self, conv=None):
        resp = conv.events.last_item(EV_PROTOCOL_RESPONSE)
        _client = conv.entity
        old_sec = _client.client_secret

        if old_sec == resp["client_secret"]:
            self._message = "Client Secret was not changed"
            self._status = self.status

        return {}


class VerifyAccessTokenResponse(Error):
    """
    Checks the Access Token response
    """
    cid = "verify-access-token-response"
    section = "http://openid.bitbucket.org/" + \
              "openid-connect-messages-1_0.html#access_token_response"

    def _func(self, conv=None):
        resp = get_protocol_response(conv, message.AccessTokenResponse)[0]

        # This specification further constrains that only Bearer Tokens [OAuth
        # .Bearer] are issued at the Token Endpoint. The OAuth 2.0 response
        # parameter "token_type" MUST be set to "Bearer".
        if "token_type" in resp and resp["token_type"].lower() != "bearer":
            self._message = "token_type has to be 'Bearer'"
            self._status = self.status

        # In addition to the OAuth 2.0 response parameters, the following
        # parameters MUST be included in the response if the grant_type is
        # authorization_code and the Authorization Request scope parameter
        # contained openid: id_token
        cis = conv.cis[-1]
        if cis["grant_type"] == "authorization_code":
            req = get_authorization_request(conv, AuthorizationRequest)
            if "openid" in req["scope"]:
                if "id_token" not in resp:
                    self._message = "ID Token has to be present"
                    self._status = self.status

        return {}


class SingleSignOn(Error):
    """ Verifies that Single-Sign-On actually works """
    cid = "single-sign-on"

    def _func(self, conv):
        resps = get_protocol_response(conv, message.AuthorizationResponse)

        if len(resps) > 1:
            self._message = " ".join(["Multiple authentications when only one",
                                      "was expected"])
            self._status = self.status

        return {}


class MultipleSignOn(Error):
    """ Verifies that multiple authentications was used in the flow """
    cid = "multiple-sign-on"
    doc = """
    :param status: Status code returned on error
    :type status: integer
    """

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res

        if len(idt) == 1:
            self._message = " ".join(["Only one authentication when more than",
                                      "one was expected"])
            self._status = self.status

        # number of ID Tokens 2 or 4, nonce and aud should be the same for
        # pairs .

        if len(idt) == 4:
            _idt = [idt[0]]
            _nonce = idt[0]["nonce"]
            _other = [i for i in idt if i["nonce"] != _nonce]
            _idt.append(_other[0])
            idt = _idt

        # verify that it is in fact two separate authentications
        try:
            assert idt[0]["auth_time"] != idt[1]["auth_time"]
        except KeyError:
            self._message = "No \"auth_time\" found in both ID tokens so it " \
                            "cannot be compared"
        except AssertionError:
            self._message = "Not two separate authentications!"
            try:
                self._status = self._kwargs["status"]
            except KeyError:
                self._status = self.status

        return {}


class SameAuthn(Error):
    """ Verifies that the same authentication was used twice in the flow. """
    cid = "same-authn"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res

        if len(idt) < 2:
            self._message = " ".join(["Expected more than one authentication",
                                      "found %d" % len(idt)])
            self._status = self.status

        # verify that the two ID Tokens are based on the same authentication
        try:
            assert idt[0]["auth_time"] == idt[1]["auth_time"]
            assert idt[0]["sub"] == idt[1]["sub"]
        except AssertionError:
            self._message = "Not one authentication!"
            self._status = self.status

        return {}


class VerifyRedirectUriQueryComponent(Error):
    """
    Checks that a query component in the redirect_uri value that was
    specified in the Authorization request are present in the
    URL used by the OP for the response.
    """
    cid = "verify-redirect_uri-query_component"
    doc = """
    :param kwargs: query components (key, value) that was expected
    :type kwargs: dictionary
    """

    def _func(self, conv):
        item = conv.events.last_item(EV_PROTOCOL_RESPONSE)

        try:
            qc = conv.query_component
        except AttributeError:
            # If code flow
            try:
                for key, vals in list(self._kwargs.items()):
                    assert item[key] == vals
            except (AssertionError, KeyError):
                self._message = "Query component that was part of the " \
                                "redirect_uri is missing"
                self._status = self.status
        else:
            # If implicit or hybrid
            qd = parse_qs(qc)
            try:
                for key, val in list(self._kwargs.items()):
                    assert qd[key] == [val]
            except (AssertionError, KeyError):
                self._message = "Query component that was part of the " \
                                "redirect_uri is missing"
                self._status = self.status

        return {}


class CheckKeys(CriticalError):
    """ Checks that the necessary keys are defined """
    cid = "check-keys"
    msg = "Missing keys"

    def _func(self, conv=None):
        # cls = conv.request_spec"].request
        client = conv.entity
        # key type
        keys = client.keyjar.get_signing_key("rsa")
        try:
            assert keys
        except AssertionError:
            self._status = self.status
            self._message = "No RSA key for signing provided"

        return {}


class VerifyPolicyURLs(Error):
    cid = "policy_uri_on_page"
    msg = "policy_uri not on page"

    def _func(self, conv=None):
        login_page = conv.login_page
        regreq = conv.RegistrationRequest

        try:
            assert regreq["policy_uri"] in login_page
        except AssertionError:
            self._status = self.status

        return {}


class VerifyLogoURLs(Error):
    cid = "logo_uri_on_page"
    msg = "logo_uri not on page"

    def _func(self, conv=None):
        login_page = conv.login_page
        regreq = conv.RegistrationRequest

        try:
            assert regreq["logo_uri"] in login_page
        except AssertionError:
            self._status = self.status

        return {}


def unequal(idt, idts):
    res = []
    for _idt in idts:
        if _idt["aud"] == idt["aud"]:
            continue
        else:
            res.append(_idt)
    return res


class CheckUserID(Error):
    """
    Verifies that the sub value differs between public and pairwise
    subject types.
    """
    cid = "different_sub"
    msg = "sub not changed between public and pairwise"

    def _func(self, conv=None):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        if len(res) < 2:
            self._status = self.status
            self._message = "Too few ID Tokens"

        # may be anywhere between 2 and 4 ID Tokens
        # weed out duplicates (ID Tokens received from authorization and the
        # token endpoint
        idt0 = res[0]
        rem = unequal(idt0, res[1:])
        if not rem:
            self._message = "Seems the same ID token was returned"
            self._status = self.status
            return {}

        idt1 = rem[0]
        if len(rem) > 1:
            # should verify that the remaining are duplicates
            rem = unequal(idt1, rem[1:])
            if rem:
                self._message = "Too many unique ID tokens"
                self._status = self.status
                return {}

        try:
            assert idt0["sub"] != idt1["sub"]
        except AssertionError:
            self._status = self.status

        return {}


class VerifyUserInfo(Error):
    """
    Checks that all required information are in the UserInfo.
    Note that it's not an error on the OPs behalf if not all information
    is there.
    """
    cid = "verify-userinfo"
    msg = "Essential UserInfo missing"

    def _func(self, conv):
        req = get_authorization_request(conv, AuthorizationRequest)
        try:
            claims = req["userinfo_claims"]["claims"]
        except KeyError:
            claims = {}
        for scope in req["scope"]:
            for param in SCOPE2CLAIMS[scope]:
                claims[param] = REQUIRED

        response = conv.events.last_item(EV_PROTOCOL_RESPONSE)
        try:
            for key, val in list(claims.items()):
                if val == OPTIONAL:
                    continue
                elif val == REQUIRED:
                    assert key in response
                else:
                    value = val["value"]
                    assert response[key] == value
        except AssertionError:
            self._status = self.status

        return {}


class CheckAsymSignedUserInfo(Error):
    """
    Verifies that the UserInfo was signed with a RSA key
    """
    cid = "asym-signed-userinfo"
    msg = "UserInfo was not signed"

    def _func(self, conv):
        jwt = get_protocol_response(conv, OpenIDSchema)[0]
        try:
            assert jwt.jws_header["alg"].startswith("RS")
        except AssertionError:
            self._status = self.status

        return {}


class CheckSymSignedIdToken(Error):
    """
    Verifies that the IDToken was signed with a symmetric key
    """
    cid = "sym-signed-idtoken"
    msg = "Incorrect signature type"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]

        try:
            assert idt.jws_header["alg"].startswith("HS")
        except AssertionError:
            self._status = self.status

        return {}


class CheckESSignedIdToken(Error):
    """
    Verifies that the ID Token was signed with a EC key
    """
    cid = "es-signed-idtoken"
    msg = "Incorrect signature type"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]
        try:
            assert idt.jws_header["alg"].startswith("ES")
        except AssertionError:
            self._status = self.status

        return {}


class CheckEncryptedUserInfo(Error):
    """
    Verifies that the UserInfo returned was encrypted
    """
    cid = "encrypted-userinfo"
    msg = "UserInfo was not encrypted"

    def _func(self, conv):
        jwt = get_protocol_response(conv, OpenIDSchema)[0]
        try:
            assert jwt.jwe_header["alg"].startswith("RSA")
        except KeyError:  # No jwe_header or no 'alg' in header
            self._status = self.status
        except AssertionError:  # No RSA crypto used
            self._status = self.status

        return {}


class CheckEncryptedIDToken(Error):
    """
    Verifies that a IDToken was encrypted
    """
    cid = "encrypted-idtoken"
    msg = "ID Token was not encrypted"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]

        try:
            assert idt.jwe_header["alg"].startswith("RSA")
        except AssertionError:
            self._status = self.status

        return {}


class CheckSignedEncryptedIDToken(Error):
    """
    Verifies that a IDToken was signed and then encrypted
    """
    cid = "signed-encrypted-idtoken"
    msg = "ID Token was not signed and encrypted"
    doc = """
    :param enc_alg: Key Encryption algorithm
    :type enc_alg: string
    :param enc_enc: Content Encryption algorithm
    :type enc_enc: string
    :param sig_alg: Signature algorithm
    :type sig_alg: string
    """

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]

        # encryption header
        try:
            assert idt.jwe_header["alg"] == self._kwargs["enc_alg"]
            assert idt.jwe_header["enc"] == self._kwargs["enc_enc"]
        except AssertionError:
            self._status = self.status

        # signature header
        try:
            assert idt.jws_header["alg"] == self._kwargs["sign_alg"]
        except AssertionError:
            self._status = self.status

        return {}


class VerifyAud(Error):
    cid = "verify-aud"
    msg = "Not the same aud in the newly issued ID Token"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        aud = [i["aud"] for i in res]
        try:
            assert aud[0] == aud[1]
        except AssertionError:
            self._status = self.status

        return {}


class VerifyImplicitResponse(Error):
    cid = "verify-implicit-reponse"
    msg = "Expected response in fragment"

    def _func(self, conv):
        _part = urlparse(conv.info)
        # first verify there is a fragment
        try:
            assert _part.fragment
            # The is where the response is
            _resp = AuthorizationResponse().from_urlencoded(_part.fragment)
            assert _resp
            # Can't do this check since in the response_message the id_token is
            # unpacked
            # assert _resp == conv.events.last_item('response')
        except AssertionError:
            self._status = self.status

        return {}


class CheckIdTokenNonce(Error):
    """
    Verify that the nonce in the IDToken is the same that's included in the
    Authorization Request.
    """
    cid = "check-idtoken-nonce"
    msg = "Expected same nonce back as sent"

    def _func(self, conv):
        try:
            _nonce = get_authorization_request(
                conv, AuthorizationRequest)["nonce"]
        except KeyError:
            pass
        else:
            try:
                idt = get_id_tokens(conv)[-1]
            except IndexError:
                pass
            else:
                try:
                    assert _nonce == idt["nonce"]
                except (AssertionError, KeyError):
                    self._status = self.status

        return {}


class CheckResponseMode(CheckOPSupported):
    """
    Checks that the asked for response mode are among the supported
    """
    cid = "check-response-mode"
    msg = "Response mode not supported"

    def _supported(self, request_args, provider_info):
        try:
            supported = provider_info["response_modes_supported"]
        except KeyError:  # default set
            supported = ['query', 'fragment']

        try:
            val = request_args["response_mode"]
            if val in supported:
                return True
            else:
                return False
        except KeyError:
            pass

        return True


class VerifyISS(Error):
    """
    verify that the iss value given in the discovery response is the
    same as the issuer in an IDToken.
    """
    cid = "verify-iss"
    msg = "Not the same iss/issuer in the id_token as in the Provider Info"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        issuer = get_provider_info(conv)["issuer"]

        for iss in [i["iss"] for i in res]:
            try:
                assert iss == issuer
            except AssertionError:
                self._status = self.status

        return {}


class VerfyMTIEncSigAlgorithms(Information):
    """
    Verify that the MTI algorithms appear.
    """
    cid = "verify-mti-enc-sig-algorithms"
    msg = "MTI encryption/signature algorithm missing"
    status = INFORMATION

    def _func(self, conv):
        _pi = get_provider_info(conv)

        missing = []
        for key, algs in list(MTI.items()):
            for alg in algs:
                try:
                    assert alg in _pi[key]
                except (AssertionError, KeyError):
                    _alg = "%s:%s" % (key, alg)
                    if _alg not in missing:
                        missing.append(_alg)

        if missing:
            self._message = "The following MTI algorithms were missing :%s" % (
                missing,)
            self._status = self.status

        return {}


class CheckEncSigAlgorithms(Information):
    """
    Verify that the ENC/SIG algorithms listed are officially registered.
    """
    cid = "check-enc-sig-algorithms"
    msg = "Unofficial algorithm"

    def _func(self, conv):
        _pi = get_provider_info(conv)

        unknown_jws = []
        for typ in ["id_token", "userinfo", "request_object",
                    "token_endpoint_auth"]:
            _claim = "%s_signing_alg_values_supported" % typ
            try:
                algs = _pi[_claim]
            except KeyError:
                pass
            else:
                for alg in algs:
                    try:
                        assert alg in REGISTERED_JWS_ALGORITHMS
                    except AssertionError:
                        if alg not in unknown_jws:
                            unknown_jws.append(alg)

        unknown_jwe_alg = []
        for typ in ["id_token", "userinfo", "request_object"]:
            _claim = "%s_encryption_alg_values_supported" % typ
            try:
                algs = _pi[_claim]
            except KeyError:
                pass
            else:
                for alg in algs:
                    try:
                        assert alg in REGISTERED_JWE_alg_ALGORITHMS
                    except AssertionError:
                        if alg not in unknown_jwe_alg:
                            unknown_jwe_alg.append(alg)

        unknown_jwe_enc = []
        for typ in ["id_token", "userinfo", "request_object"]:
            _claim = "%s_encryption_enc_values_supported" % typ
            try:
                algs = _pi[_claim]
            except KeyError:
                pass
            else:
                for alg in algs:
                    try:
                        assert alg in REGISTERED_JWE_enc_ALGORITHMS
                    except AssertionError:
                        if alg not in unknown_jwe_enc:
                            unknown_jwe_enc.append(alg)

        if unknown_jws or unknown_jwe_alg or unknown_jwe_enc:
            _txt = "Used algorithms that are not registered: "
            flag = False
            if unknown_jws:
                _txt += "JWS algorithms:%s" % (unknown_jws,)
                flag = True
            if unknown_jwe_alg:
                if flag:
                    _txt += ", "
                _txt += "JWE alg algorithms:%s" % (unknown_jwe_alg,)
                flag = True
            if unknown_jwe_enc:
                if flag:
                    _txt += ", "
                _txt += "JWE enc algorithms:%s" % (unknown_jwe_enc,)

            self._message = _txt
            self._status = self.status

        return {}


class VerifyOPEndpointsUseHTTPS(Error):
    """
    Verify that all OP endpoints uses https
    """
    cid = "verify-op-endpoints-use-https"
    msg = "Some OP endpoints are not using HTTPS"

    def _func(self, conv):
        m = []
        _pi = get_provider_info(conv)
        for param, val in list(_pi.items()):
            if param.endswith("_endpoint"):
                try:
                    assert val.startswith("https://")
                except AssertionError:
                    self._status = self.status
                    m.append(param)
        if m:
            self._message = 'These endpoint do not use HTTPS: {}'.format(m)

        return {}


class VerifyHTTPSUsage(Error):
    """
    Verify that specific endpoints uses https
    """
    cid = "verify-https-usage"
    msg = "Some OP endpoints are not using HTTPS"
    doc = """
    :param endpoints: Which OP endpoints that should be checked
    :type endpoints: list of strings
    """

    def _func(self, conv):
        _pi = get_provider_info(conv)
        _not_https = []
        for endp in self._kwargs['endpoints']:
            try:
                assert _pi[endp].startswith("https://")
            except KeyError:
                pass
            except AssertionError:
                self._status = self.status
                _not_https.append(endp)
                break

        if _not_https:
            self._message = "{} did not use HTTPS".format(_not_https)

        return {}


class VerifyOPHasRegistrationEndpoint(Error):
    """
    Verify that the OP has a registration endpoint
    """
    cid = "verify-op-has-registration-endpoint"
    msg = "No registration endpoint"

    def _func(self, conv):
        _pi = get_provider_info(conv)
        try:
            assert "registration_endpoint" in _pi
        except AssertionError:
            self._status = self.status

        return {}


# class VerifyProviderHasDynamicClientEndpoint(Error):
#     """
#     Verify that the OP has a registration endpoint
#     """
#     cid = "verify-op-has-dynamic-client-endpoint"
#     msg = "No registration endpoint"
#
#     def _func(self, conv):
#         _pi = get_provider_info(conv)
#         try:
#             assert "dynamic_client_endpoint" in _pi
#         except AssertionError:
#             self._status = self.status
#
#         return {}


class VerifyIDTokenUserInfoSubSame(Information):
    """
    Verify that the sub claim in the ID Token is the same as is provider in
    the userinfo
    """
    cid = "verify-id_token-userinfo-same-sub"
    msg = "Sub identifier differs between the ID Token and the UserInfo"

    def _func(self, conv):
        ui_sub = ""

        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt_sub = [i["sub"] for i in res]

        # The UserInfo sub
        for instance in get_protocol_response(conv, OpenIDSchema):
            ui_sub = instance["sub"]

        try:
            assert ui_sub == idt_sub
        except AssertionError:
            self._status = self.status

        return {}


class VerifyState(Information):
    """
    Verifies that the State variable is the same returned as was sent
    """
    cid = "verify-state"
    msg = "The state value returned not the same as sent"

    def _func(self, conv):
        # The send state
        _send_state = get_authorization_request(
            conv, AuthorizationRequest)["state"]
        # the received state
        inst = get_protocol_response(conv, AuthorizationResponse)[-1]
        _recv_state = inst["state"]

        try:
            assert _send_state == _recv_state
        except AssertionError:
            self.status = self._status

        return {}


class VerifySignedIdTokenHasKID(Error):
    """
    Verifies that the header of a signed IDToken includes a kid claim.
    """
    cid = "verify-signed-idtoken-has-kid"
    msg = "Signed ID Token has no kid"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]
        # doesn't verify signing kid if JWT is signed and then encrypted
        if "enc" not in idt.jws_header:
            if idt.jws_header["alg"].startswith("RS"):
                try:
                    assert "kid" in idt.jws_header
                except AssertionError:
                    self._message = "%s: header=%s" % (self.msg, idt.jws_header)
                    self._status = self.status

        return {}


class VerifySignedIdToken(Error):
    """
    Verifies that an ID Token is signed
    """
    cid = "verify-idtoken-is-signed"
    msg = "ID Token unsigned or signed with the wrong algorithm"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        used_alg = res[-1].jws_header["alg"]  # The last ID Token I got

        try:
            expected_alg = self._kwargs["alg"]
        except KeyError:
            self._message = "Signature algorithm='%s'".format(used_alg)
        else:
            try:
                assert used_alg == expected_alg
            except AssertionError:
                self._status = self.status
            else:
                self._message = "Signature algorithm='%s'".format(used_alg)

        return {}


class VerifyNonce(Error):
    """
    Verifies that the nonce received in the IDToken is the same as was
    given in the Authorization Request
    """
    cid = "verify-nonce"
    msg = "Not the same nonce in the ID Token as in the authorization request"

    def _func(self, conv):
        authz_req = conv.events.get_data(EV_REDIRECT_URL)[0].split('?')[1]
        req = parse_qs(authz_req)
        try:
            ar_nonce = req['nonce'][0]
        except KeyError:
            ar_nonce = ''

        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]
        try:
            idt_nonce = idt["nonce"]
        except KeyError:
            idt_nonce = ""

        try:
            assert ar_nonce == idt_nonce
        except AssertionError:
            self._status = self.status

        return {}


class VerifyUnSignedIdToken(Error):
    """
    Verifies that an IDToken is in fact unsigned, that is signed with the
    'none' algorithm.
    """
    cid = "unsigned-idtoken"
    msg = "Unsigned ID Token"

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]
        try:
            assert idt.jws_header["alg"] == "none"
        except AssertionError:
            self._status = self.status

        return {}


class CheckSubConfig(Error):
    cid = "sub-claim-configured"
    msg = "sub claim not configured"

    def _func(self, conv):
        try:
            _ = conv.entity_config["sub"]
        except KeyError:
            self._status = self.status

        return {}


class VerifySubValue(Error):
    """
    Verifies that the sub claim returned in the id_token matched the one
    asked for.
    """
    cid = "verify-sub-value"
    msg = "Unexpected sub value"

    def _func(self, conv):
        # Use the last Authorization Request
        areq = get_protocol_request(conv, AuthorizationRequest)[-1]
        try:
            sub = areq["claims"]["id_token"]["sub"]["value"]
        except KeyError:
            try:
                _claims = areq['claims']
            except KeyError:
                self._message = 'No claims request in the Authorization request'
            else:
                try:
                    _ic = _claims['id_token']
                except KeyError:
                    self._message = 'No id_token claims request'
                else:
                    try:
                        _sub = _ic['sub']
                    except KeyError:
                        self._message = 'No claims request for "sub" in ' \
                                        'id_token'
                    else:
                        self._message = 'Faulty claims request'
            self._status = self.status
            return {}

        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]
        idt_sub = idt["sub"]
        try:
            assert idt_sub == sub
        except AssertionError:
            self._status = self.status

        return {}


class VerifyDifferentSub(Error):
    """
    Verifies that the sub claim returned in the id_token matched the
    asked for.
    """
    cid = "verify-different-sub"
    msg = "Verify different RPs get different sub values"

    def _func(self, conv):
        sub = [idt["sub"] for idt in get_id_tokens(conv)]

        try:
            assert sub[0] != sub[1]
        except AssertionError:
            self._status = self.status
        except IndexError:
            self._message = "Not enough sub values"
            self._status = self.status

        return {}


class VerifyBase64URL(Check):
    """
    Verifies that the base64 encoded parts of a JWK is in fact base64url
    encoded and not just base64 encoded
    """
    cid = "verify-base64url"
    msg = "JWK not according to the spec"

    @staticmethod
    def _chk(key, params):
        st = OK
        txt = []
        for y in params:
            try:
                base64url_to_long(key[y].encode('utf8'))
            except ValueError:
                st = WARNING
                if "kid" in key:
                    txt.append(
                        "'%s' not base64url encoded in key with kid '%s'" % (
                            y, key["kid"]))
                else:
                    txt.append(
                        "'%s' not base64url encoded in %s key" % (
                            y, key["kty"]))
        return st, txt

    def _func(self, conv):
        pi = get_provider_info(conv)
        resp = conv.entity.http_request(pi["jwks_uri"], verify=False,
                                        allow_redirects=True)

        try:
            err_status = self._kwargs["err_status"]
        except KeyError:
            err_status = WARNING

        if resp.status_code == 200:
            jwks = json.loads(resp.text)
            txt = []
            s = OK
            key = {}  # Just to get rid of 'undefined' warning
            try:
                for key in jwks["keys"]:
                    _txt = []
                    _st = OK
                    if key["kty"] == "RSA":
                        _st, _txt = self._chk(key, ["e", "n"])
                    elif key["kty"] == "EC":
                        _st, _txt = self._chk(key, ["x", "y"])
                    txt.extend(_txt)
                    if s < _st <= CRITICAL:
                        s = _st
            except KeyError:
                self._status = err_status
                self._message = "Missing bare key info on %s key" % key["kty"]
            else:
                if s != OK:
                    self._status = err_status
                    self._message = "\n".join(txt)
        else:
            self._status = err_status
            self._message = "Could not load JWK Set from {}".format(
                pi["jwks_uri"])

        return {}


class DiscoveryConfig(Error):
    """
    Verifies that an endpoint for Provider Info discovery is configured
    """
    cid = "support-discovery"
    msg = "OP does not support discovery"

    def _func(self, conv):
        try:
            conv.entity_config["srv_discovery_url"]
        except KeyError:
            self._status = ERROR

        return {}


class NewSigningKeys(Warnings):
    """
    Verifies that two set of signing keys are not the same
    """
    cid = "new-signing-keys"
    msg = "Did not detect any change in signing keys"

    def _func(self, conv):
        kbl1 = conv.keybundle[0].available_keys()  # The old
        kbl2 = conv.keybundle[1].available_keys()  # The new

        sign_key0 = [k for k in kbl1 if k.use == "sig"]
        sign_key1 = [k for k in kbl2 if k.use == "sig"]

        new = 0
        for key in sign_key1:
            _new = True
            for _key in sign_key0:
                if key.kty == _key.kty:  # Same type of key
                    if key == _key:
                        _new = False
                        break
            if _new:
                new += 1
        if not new:
            self._status = self.status

        return {}


class NewEncryptionKeys(Warnings):
    """
    Verifies that two set of encryption keys are not the same
    """
    cid = "new-encryption-keys"
    msg = "Did not detect any change in encryption keys"

    def _func(self, conv):
        kbl1 = conv.keybundle[0].available_keys()  # The old
        kbl2 = conv.keybundle[1].available_keys()  # The new
        sign_key0 = [k for k in kbl1 if k.use == "enc"]
        sign_key1 = [k for k in kbl2 if k.use == "enc"]

        new = 0
        for key in sign_key1:
            _new = True
            for _key in sign_key0:
                if key.kty == _key.kty:  # Same type of key
                    if key == _key:
                        _new = False
                        break
            if _new:
                new += 1

        if not new:
            self._status = self.status

        return {}


class UsedAcrValue(Check):
    """
    The acr value in the ID Token
    """
    cid = "used-acr-value"
    msg = ""

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = WARNING
            return {}

        try:
            pref = get_authorization_request(
                conv, AuthorizationRequest)["acr_values"]
        except KeyError:
            pref = []

        idt = res[-1]
        try:
            _acr = idt["acr"]
        except KeyError:
            self._message = "No acr value present in the ID Token"
            self._status = WARNING
        else:
            if _acr not in pref:
                self._status = WARNING
            self._message = "Used acr value: %s, preferred: %s" % (_acr, pref)

        return {}


class IsIDTokenSigned(Information):
    """
    Checks if the id_token is signed
    """
    cid = "is-idtoken-signed"
    msg = ""

    def _func(self, conv):
        res = get_id_tokens(conv)
        if not res:
            self._message = "No response to get the ID Token from"
            self._status = self.status
            return {}

        idt = res[-1]
        try:
            _alg = idt.jws_header["alg"]
        except (KeyError, TypeError):
            self._message = "ID Token not signed"
        else:
            self._message = "ID Token signed using alg={}".format(_alg)

        return {}


class ClaimsCheck(Information):
    """
    Checks if specific claims is present or not
    """
    cid = "claims-check"
    msg = ""
    doc = """
    :param id_token: Claims that should be present in the id_token
    :type id_token: list of strings
    :param required: If the claims are required
    :type required: boolean
    """

    def _func(self, conv):
        try:
            _claims = self._kwargs["id_token"]
        except KeyError:
            pass
        else:
            answers = get_id_tokens(conv)
            # may be more then one, use the last.
            inst = answers[-1]
            missing = []
            stat = 0
            for claim in _claims:
                try:
                    assert claim in inst
                except AssertionError:
                    if self._kwargs["required"]:
                        stat = ERROR
                    else:
                        stat = WARNING
                    missing.append(claim)

            if missing:
                self._status = stat
                self._message = "Missing claims: %s" % missing

        return {}


class BareKeys(Information):
    """
    Dynamic OPs MUST publish their public keys as bare JWK keys
    """
    cid = "bare-keys"
    msg = ""

    def _func(self, conv):
        pi = get_provider_info(conv)
        resp = conv.entity.http_request(pi["jwks_uri"], verify=False,
                                        allow_redirects=True)

        if resp.status_code == 200:
            jwks = json.loads(resp.text)
            conv.events.store('JWKS', jwks)
            key = {}
            try:
                for key in jwks["keys"]:
                    if key["kty"] == "RSA":
                        assert "n" in key and "e" in key
                    elif key["kty"] == "EC":
                        assert "x" in key and "y" in key
            except AssertionError:
                self._status = WARNING
                self._message = "Missing bare key info on {} key".format(
                    key["kty"])
        else:
            self._status = WARNING
            self._message = "Could not load JWK Set from {}".format(
                pi["jwks_uri"])

        return {}


class CheckQueryPart(Error):
    """
    Check that a query part send in the Authorization Request is returned in the
    Authorization response.
    """
    cid = "check-query-part"
    msg = ""
    doc = """
    :param kwargs: key-value pairs that should be present in the query part
    :type kwargs: dictionary
    """

    def _func(self, conv):
        areq = get_protocol_request(conv, AuthorizationRequest)[0]
        if areq['response_type'] == ['code']:
            inst = get_protocol_response(conv, AuthorizationResponse)[0]
        else:
            inst = None
            for _ev in conv.events.get(EV_HTTP_ARGS):
                if _ev.ref == 'authz_cb':
                    inst = _ev.data
                    break
            if inst is None:
                self._status = ERROR
                self._message = 'Query parameters missing'
                return {}

        for key, val in list(self._kwargs.items()):
            try:
                assert inst[key] == val
            except (AssertionError, KeyError):
                self._status = ERROR
                self._message = \
                    "The query component {}={} not part of the response".format(
                        key, val)
        return {}


VS_LINE = "The following claims were missing from the returned information: {}"


class VerifyScopes(Warnings):
    """
    Verifies that the claims corresponding to the requested scopes are returned
    """
    cid = "verify-scopes"
    msg = ""

    def _func(self, conv):
        areq = get_authorization_request(conv, AuthorizationRequest)

        # turn scopes into claims
        claims = []
        for scope in areq["scope"]:
            try:
                claims.extend([name for name in SCOPE2CLAIMS[scope]])
            except KeyError:
                pass

        if areq["response_type"] == ["id_token"]:
            # Then everything should be in the ID Token
            aresp = get_protocol_response(conv, AuthorizationResponse)[-1]
            container = aresp["id_token"]
        else:  # In Userinfo
            container = get_protocol_response(conv, OpenIDSchema)[-1]

        missing = [c for c in claims if c not in container]

        if missing:
            self._status = self.status
            self._message = VS_LINE.format(missing)

        return {}


def request_times(conv, endpoint):
    res = []

    where = conv.entity.provider_info[endpoint]

    for url, when in conv.timestamp:
        if url.startswith(where):
            res.append(when)

    return res


class AuthTimeCheck(Warnings):
    """ Check that the auth_time returned in the ID Token is in the
    expected range."""
    cid = "auth_time-check"
    doc = """
    :param max_age: Maximum age of the id_token
    :type max_age: int
    :param skew: The allowed skew in seconds
    :type skew: int
    """

    def _func(self, conv):
        res = get_id_tokens(conv)

        # only interested in the last ID Token
        idt = res[-1]

        max_age = self._kwargs["max_age"]

        # last authentication request
        sent = conv.events.when(EV_PROTOCOL_REQUEST, AuthorizationRequest)[-1]
        low = int(sent - max_age)
        low -= self._kwargs['skew']

        now = utc_time_sans_frac()
        high = now + self._kwargs['skew']

        try:
            _auth_time = int(idt["auth_time"])
        except KeyError:  # not having a auth_time is an error
            self._status = ERROR
            self._message = "There is no auth_time claim in the ID Token."
        else:
            try:
                # T0 - max_age - S <= auth_time <= T1 + S
                assert low <= _auth_time <= high
            except AssertionError:
                _range = "{} - {}".format(low, high)
                self._status = WARNING
                self._message = \
                    "auth_time [{}] not in the expected range: {}".format(
                        _auth_time, _range)
        return {}


class ValidCode(Error):
    """ Check that the authorization response contained a valid access code."""
    cid = "valid_code"

    def _func(self, conv):
        _grants = list(conv.entity.grant.keys())
        try:
            assert len(_grants) == 1
            assert conv.entity.grant[_grants[0]].is_valid()
        except AssertionError:
            self._status = ERROR
            self._message = "No valid access code"

        return {}


class GotIdTokenClaims(Warnings):
    """ Verify that I got the claims I asked for """
    cid = 'got_id_token_claims'
    _msg_pat = "The following claims didn't make it to the Id Token: {}"
    doc = """
    :param claims: claims expected to be in the id_token
    :type claims: list of strings
    """

    def _func(self, conv):
        res = get_id_tokens(conv)
        idt = res[0]
        missing = []
        for key in self._kwargs['claims']:
            if key not in idt:
                missing.append(key)
        if missing:
            self._status = WARNING
            self._message = self._msg_pat.format(missing)
        return {}


class GotUserinfoClaims(Warnings):
    """ Verify that I got the claims I asked for """
    cid = 'got_userinfo_claims'
    _msg_pat = "The following claims didn't make it to the UserInfo: {}"
    doc = """
    :param claims: claims expected to be among the user info
    :type claims: list of strings
    """

    def _func(self, conv):
        res = get_protocol_response(conv, OpenIDSchema)
        userinfo = res[0]
        missing = []
        for key in self._kwargs['claims']:
            if key not in userinfo:
                missing.append(key)
        if missing:
            self._status = WARNING
            self._message = self._msg_pat.format(missing)
        return {}


class Got(Error):
    """ Verify that I got the item I expected """
    cid = 'got'
    _msg_pat = "The following attributes didn't make it to the {}"
    doc = """
    :param where: In which protocol response the claims should occur
    :type where: string
    :param what: Which claims 
    :type what: string
    """

    def _func(self, conv):
        res = get_protocol_response(conv, OIDCMessageFactory.get_response_type('test'))
        response = res[0]
        missing = []
        for key in self._kwargs['what']:
            if key not in response:
                missing.append(key)
        if missing:
            self._status = ERROR
            self._message = self._msg_pat.format(self._kwargs['where'])
        return {}


def factory(cid):
    for name, obj in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(obj) and issubclass(obj, Check):
            try:
                if obj.cid == cid:
                    return obj
            except AttributeError:
                pass

    # Hierarchy of tests, going upwards ..
    from otest.aus import check
    return check.factory(cid)


if __name__ == "__main__":
    chk = factory("check-http-response")
    print(chk)
