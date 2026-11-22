import * as AuthSession from "expo-auth-session";
import * as Google from "expo-auth-session/providers/google";
import * as WebBrowser from "expo-web-browser";
import React, { useEffect, useRef } from "react";
import { Platform, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { SocialProvider, SocialProviders } from "../app/AuthContext";
import { colors, fonts, theme } from "../constants/theme";

// Finishes the browser round-trip when the OS hands the redirect back to us.
WebBrowser.maybeCompleteAuthSession();

// The native Google client IDs are created per platform (and tied to the
// app's bundle id / package name), so they're build-time config rather than
// something the backend can hand out. They must also be in the backend's
// GOOGLE_OAUTH_EXTRA_CLIENT_IDS, or the token's audience is rejected. When
// unset, the Google button simply isn't shown on that platform. Web (used
// for local testing) uses the web client ID the backend already reports.
const GOOGLE_IOS_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
const GOOGLE_ANDROID_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID;

const MICROSOFT_AUTHORITY = "https://login.microsoftonline.com/common/v2.0";

interface Props {
  providers: SocialProviders;
  disabled?: boolean;
  /** Called with the provider's ID token; the caller swaps it for a session. */
  onToken: (provider: SocialProvider, idToken: string) => void;
  onError: (message: string) => void;
}

const FAILED = "Kunne ikke logge inn. Prøv igjen.";

// Each button is its own component because the provider hooks must only be
// called when that provider can actually work on this platform (the Google
// hook throws if the platform's client ID is missing).
const GoogleButton = ({
  webClientId,
  disabled,
  onToken,
  onError,
}: Pick<Props, "disabled" | "onToken" | "onError"> & { webClientId: string }) => {
  const [request, response, promptAsync] = Google.useIdTokenAuthRequest({
    iosClientId: GOOGLE_IOS_CLIENT_ID,
    androidClientId: GOOGLE_ANDROID_CLIENT_ID,
    webClientId,
  });

  // The latest callbacks, without re-running the effect on every render.
  const callbacks = useRef({ onToken, onError });
  callbacks.current = { onToken, onError };

  useEffect(() => {
    if (!response) return;
    if (response.type === "success") {
      const idToken = response.params?.id_token ?? response.authentication?.idToken;
      if (idToken) callbacks.current.onToken("google", idToken);
      else callbacks.current.onError(FAILED);
    } else if (response.type === "error") {
      callbacks.current.onError(FAILED);
    }
    // "cancel" / "dismiss": the person backed out -- nothing to report.
  }, [response]);

  return (
    <TouchableOpacity
      style={[styles.button, (!request || disabled) && styles.disabled]}
      disabled={!request || disabled}
      onPress={() => promptAsync()}
    >
      <Text style={styles.buttonText}>Logg inn med Google</Text>
    </TouchableOpacity>
  );
};

const MicrosoftButton = ({
  clientId,
  disabled,
  onToken,
  onError,
}: Pick<Props, "disabled" | "onToken" | "onError"> & { clientId: string }) => {
  const discovery = AuthSession.useAutoDiscovery(MICROSOFT_AUTHORITY);
  const redirectUri = AuthSession.makeRedirectUri({ scheme: "henningsalternativjul", path: "auth" });
  const [request, response, promptAsync] = AuthSession.useAuthRequest(
    {
      clientId,
      scopes: ["openid", "profile", "email"],
      redirectUri,
      responseType: AuthSession.ResponseType.Code,
      usePKCE: true,
      extraParams: { prompt: "select_account" },
    },
    discovery
  );

  const callbacks = useRef({ onToken, onError });
  callbacks.current = { onToken, onError };

  useEffect(() => {
    if (!response) return;
    if (response.type === "error") {
      callbacks.current.onError(FAILED);
      return;
    }
    if (response.type !== "success" || !request?.codeVerifier || !discovery) return;

    AuthSession.exchangeCodeAsync(
      {
        clientId,
        code: response.params.code,
        redirectUri,
        extraParams: { code_verifier: request.codeVerifier },
      },
      discovery
    )
      .then((tokens) => {
        if (tokens.idToken) callbacks.current.onToken("microsoft", tokens.idToken);
        else callbacks.current.onError(FAILED);
      })
      .catch(() => callbacks.current.onError(FAILED));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per response
  }, [response]);

  return (
    <TouchableOpacity
      style={[styles.button, (!request || disabled) && styles.disabled]}
      disabled={!request || disabled}
      onPress={() => promptAsync()}
    >
      <Text style={styles.buttonText}>Logg inn med Microsoft</Text>
    </TouchableOpacity>
  );
};

export default function SocialLoginButtons({ providers, disabled, onToken, onError }: Props) {
  const googleWorksHere =
    !!providers.google &&
    (Platform.OS === "ios" ? !!GOOGLE_IOS_CLIENT_ID : Platform.OS === "android" ? !!GOOGLE_ANDROID_CLIENT_ID : true);

  if (!googleWorksHere && !providers.microsoft) return null;

  return (
    <View style={styles.container}>
      <Text style={styles.divider}>eller</Text>
      {googleWorksHere && providers.google && (
        <GoogleButton webClientId={providers.google.client_id} disabled={disabled} onToken={onToken} onError={onError} />
      )}
      {providers.microsoft && (
        <MicrosoftButton clientId={providers.microsoft.client_id} disabled={disabled} onToken={onToken} onError={onError} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: 20, gap: 10 },
  divider: {
    textAlign: "center",
    fontFamily: fonts.bodyMedium,
    fontSize: 13,
    color: colors.ink400,
    textTransform: "uppercase",
  },
  button: {
    backgroundColor: colors.white,
    borderWidth: 1.5,
    borderColor: colors.cream200,
    paddingVertical: 13,
    borderRadius: 999,
    alignItems: "center",
  },
  buttonText: { fontFamily: fonts.bodySemiBold, fontSize: 15, color: theme.text },
  disabled: { opacity: 0.5 },
});
