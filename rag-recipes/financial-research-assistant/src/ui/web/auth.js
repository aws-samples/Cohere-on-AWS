// Amazon Cognito sign-in without an SDK: plain calls to the Cognito API over HTTPS.
// Demo simplification: the password flow (USER_PASSWORD_AUTH) is used instead of SRP, and tokens live in
// sessionStorage (cleared when the tab closes). The Content-Security-Policy set by CloudFront limits scripts to this site.
const cfg = window.FRW_CONFIG || {};
const STORE = "frw.session";

async function cognito(target, body) {
  const res = await fetch(`https://cognito-idp.${cfg.region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || "Sign-in failed.");
  return data;
}

function decodeClaims(jwt) {
  try {
    const part = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), "=")));
  } catch {
    return {};
  }
}

function save(result, previous = {}) {
  const idToken = result.IdToken;
  const session = {
    idToken,
    refreshToken: result.RefreshToken || previous.refreshToken,
    expiresAt: Date.now() + (result.ExpiresIn || 3600) * 1000,
    email: decodeClaims(idToken).email || "",
  };
  sessionStorage.setItem(STORE, JSON.stringify(session));
  return session;
}

function load() {
  try {
    return JSON.parse(sessionStorage.getItem(STORE)) || null;
  } catch {
    return null;
  }
}

/** Returns {done: true} or {challenge: "NEW_PASSWORD_REQUIRED", session, username}. */
export async function signIn(email, password) {
  const out = await cognito("InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: cfg.userPoolClientId,
    AuthParameters: { USERNAME: email, PASSWORD: password },
  });
  if (out.ChallengeName === "NEW_PASSWORD_REQUIRED") {
    return { challenge: out.ChallengeName, session: out.Session, username: email };
  }
  if (!out.AuthenticationResult) throw new Error("This account needs a step that the demo does not support.");
  save(out.AuthenticationResult);
  return { done: true };
}

export async function completeNewPassword(pending, newPassword) {
  const out = await cognito("RespondToAuthChallenge", {
    ClientId: cfg.userPoolClientId,
    ChallengeName: "NEW_PASSWORD_REQUIRED",
    Session: pending.session,
    ChallengeResponses: { USERNAME: pending.username, NEW_PASSWORD: newPassword },
  });
  if (!out.AuthenticationResult) throw new Error("Could not set the new password.");
  save(out.AuthenticationResult);
}

/** A valid ID token, refreshed when it is about to expire; null when the user must sign in again. */
export async function getIdToken() {
  const s = load();
  if (!s) return null;
  if (s.expiresAt - Date.now() > 60000) return s.idToken;
  if (!s.refreshToken) return signOut();
  try {
    const out = await cognito("InitiateAuth", {
      AuthFlow: "REFRESH_TOKEN_AUTH",
      ClientId: cfg.userPoolClientId,
      AuthParameters: { REFRESH_TOKEN: s.refreshToken },
    });
    return save(out.AuthenticationResult, s).idToken;
  } catch {
    return signOut();
  }
}

export function currentEmail() {
  return (load() || {}).email || "";
}

export function signOut() {
  sessionStorage.removeItem(STORE);
  return null;
}
