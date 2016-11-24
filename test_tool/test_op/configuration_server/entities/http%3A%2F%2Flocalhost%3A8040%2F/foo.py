TOOL = {
    "instance_id": "basic_test_1",
    "profile": "C.T.T.T.s.",
    "issuer": "https://localhost:8040/"
}

CLIENT = {
    "behaviour": {
        "scope": ["openid", "profile", "email", "address", "phone"]
    },
    "client_prefs": {
        "default_max_age": 3600,
        "grant_types": [
            "authorization_code", "implicit", "refresh_token",
            "urn:ietf:params:oauth:grant-type:jwt-bearer:"],
        "id_token_signed_response_alg": [
            "RS256", "RS384", "RS512", "HS512", "HS384", "HS256"
        ],
        "request_object_signing_alg": [
            "RS256", "RS384", "RS512", "HS512", "HS384", "HS256"
        ],
        "require_auth_time": True,
        "response_types": [
            "code", "token", "id_token", "token id_token",
            "code id_token", "code token", "code token id_token"
        ],
        "subject_type": "public",
        "token_endpoint_auth_method": [
            "client_secret_basic", "client_secret_post",
            "client_secret_jwt", "private_key_jwt"
        ],
        "userinfo_signed_response_alg": [
            "RS256", "RS384", "RS512", "HS512", "HS384", "HS256"
        ],
    },
    "registration_info": {
        "application_name": "OIC test tool",
        "application_type": "web",
        "redirect_uris": ["{}/authz_cb"],
        "contacts": ["roland@example.com"],
        "post_logout_redirect_uris": ["{}/logout"]
    }
}