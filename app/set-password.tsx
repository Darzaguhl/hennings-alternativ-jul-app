// app/set-password.tsx
//
// Two modes in one screen:
// - Opened with a ?token= param (e.g. a henningsalternativjul://set-password
//   deep link from the password-setup email, or navigated to from the
//   website's "Open in app" button): preview the token, then let the
//   volunteer pick a password right here.
// - Opened with no token (from the "Glemt passord, eller ny bruker?" link
//   on the login screen): let them request a fresh link by email instead
//   -- covers both a brand new passwordless volunteer and someone who had
//   a password and forgot it. Always shows the same generic confirmation,
//   matching the backend's anti-enumeration stance -- see
//   api.request_password_setup.
import { useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { colors, fonts, theme } from "../constants/theme";
import { useAuth } from "./AuthContext";

export default function SetPasswordScreen() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const { apiFetch } = useAuth();
  const router = useRouter();

  const [checking, setChecking] = useState(!!token);
  const [previewEmail, setPreviewEmail] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  const [requestEmail, setRequestEmail] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [requested, setRequested] = useState(false);

  useEffect(() => {
    if (!token) return;
    let active = true;
    (async () => {
      try {
        const response = await apiFetch(`/api/password-setup/${token}/`);
        const body = await response.json().catch(() => ({}));
        if (!active) return;
        if (!response.ok || !body.is_usable) {
          setPreviewError("Denne lenken er ikke lenger gyldig — den kan være brukt eller utløpt.");
        } else {
          setPreviewEmail(body.email);
        }
      } catch (err) {
        console.error("Error loading password setup link", err);
        if (active) setPreviewError("Kunne ikke laste denne lenken akkurat nå. Prøv igjen.");
      } finally {
        if (active) setChecking(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [token, apiFetch]);

  const handleSetPassword = useCallback(async () => {
    if (password.length < 8) {
      Alert.alert("For kort", "Passordet må være minst 8 tegn.");
      return;
    }
    if (password !== passwordConfirm) {
      Alert.alert("Feil", "Passordene er ikke like.");
      return;
    }
    setSubmitting(true);
    try {
      const response = await apiFetch("/api/password-setup/confirm/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = body?.password?.[0] ?? body?.token?.[0] ?? body?.detail ?? "Kunne ikke sette passord.";
        throw new Error(message);
      }
      setDone(true);
    } catch (err: any) {
      Alert.alert("Feil", err?.message ?? "Noe gikk galt.");
    } finally {
      setSubmitting(false);
    }
  }, [apiFetch, token, password, passwordConfirm]);

  const handleRequestLink = useCallback(async () => {
    if (!requestEmail.trim()) return;
    setRequesting(true);
    try {
      await apiFetch("/api/password-setup/request/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: requestEmail.trim() }),
      });
    } catch (err) {
      console.error("Error requesting password setup link", err);
      // Fall through to the same generic message either way -- we never
      // want to reveal whether the email is registered.
    } finally {
      setRequesting(false);
      setRequested(true);
    }
  }, [apiFetch, requestEmail]);

  const backToLogin = () => router.replace("/login");

  let body: React.ReactNode;

  if (token) {
    if (checking) {
      body = <ActivityIndicator color={theme.primary} style={styles.spinner} />;
    } else if (done) {
      body = (
        <>
          <Text style={styles.message}>
            Passordet er satt! Du kan nå logge inn med denne e-posten og passordet du nettopp valgte.
          </Text>
          <TouchableOpacity style={styles.button} onPress={backToLogin}>
            <Text style={styles.buttonText}>Gå til innlogging</Text>
          </TouchableOpacity>
        </>
      );
    } else if (previewError) {
      body = (
        <>
          <Text style={styles.errorText}>{previewError}</Text>
          <TouchableOpacity style={styles.button} onPress={() => router.replace("/set-password")}>
            <Text style={styles.buttonText}>Be om en ny lenke</Text>
          </TouchableOpacity>
        </>
      );
    } else {
      body = (
        <>
          <Text style={styles.message}>Sett et passord for {previewEmail} — så kan du logge inn i appen.</Text>
          <TextInput
            style={styles.input}
            placeholder="Nytt passord"
            placeholderTextColor={colors.ink400}
            secureTextEntry
            autoComplete="new-password"
            value={password}
            onChangeText={setPassword}
          />
          <TextInput
            style={styles.input}
            placeholder="Bekreft passord"
            placeholderTextColor={colors.ink400}
            secureTextEntry
            autoComplete="new-password"
            value={passwordConfirm}
            onChangeText={setPasswordConfirm}
          />
          <TouchableOpacity style={styles.button} onPress={handleSetPassword} disabled={submitting}>
            {submitting ? <ActivityIndicator color={theme.primaryDark} /> : <Text style={styles.buttonText}>Sett passord</Text>}
          </TouchableOpacity>
        </>
      );
    }
  } else if (requested) {
    body = (
      <Text style={styles.message}>
        Hvis kontoen finnes, har vi sendt en lenke til {requestEmail.trim()}. Sjekk e-posten din.
      </Text>
    );
  } else {
    body = (
      <>
        <Text style={styles.message}>
          Skriv inn e-posten du meldte deg på med, så sender vi deg en lenke for å sette (eller nullstille) et
          passord.
        </Text>
        <TextInput
          style={styles.input}
          placeholder="E-post"
          placeholderTextColor={colors.ink400}
          autoCapitalize="none"
          keyboardType="email-address"
          autoComplete="email"
          value={requestEmail}
          onChangeText={setRequestEmail}
        />
        <TouchableOpacity style={styles.button} onPress={handleRequestLink} disabled={requesting || !requestEmail.trim()}>
          {requesting ? <ActivityIndicator color={theme.primaryDark} /> : <Text style={styles.buttonText}>Send lenke</Text>}
        </TouchableOpacity>
      </>
    );
  }

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Sett et passord</Text>
        {body}
        <TouchableOpacity onPress={backToLogin} style={styles.backLink}>
          <Text style={styles.backLinkText}>Tilbake til innlogging</Text>
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  container: { flexGrow: 1, justifyContent: "center", padding: 24, backgroundColor: theme.background },
  title: {
    fontSize: 22,
    fontFamily: fonts.displayBold,
    marginBottom: 20,
    textAlign: "center",
    color: theme.primaryDark,
  },
  message: { fontFamily: fonts.body, fontSize: 15, color: theme.text, marginBottom: 20, textAlign: "center" },
  errorText: { fontFamily: fonts.body, fontSize: 15, color: theme.danger, marginBottom: 20, textAlign: "center" },
  spinner: { marginBottom: 20 },
  input: {
    borderWidth: 1.5,
    borderColor: colors.cream200,
    backgroundColor: colors.white,
    padding: 14,
    marginBottom: 14,
    borderRadius: 10,
    fontFamily: fonts.body,
    fontSize: 15,
    color: theme.text,
  },
  button: {
    backgroundColor: theme.accent,
    paddingVertical: 14,
    borderRadius: 999,
    alignItems: "center",
    marginTop: 8,
  },
  buttonText: { fontFamily: fonts.bodySemiBold, fontSize: 16, color: theme.primaryDark },
  backLink: { marginTop: 24, alignItems: "center" },
  backLinkText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: theme.primary },
});
