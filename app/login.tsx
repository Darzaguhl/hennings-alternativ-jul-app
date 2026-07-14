// app/login.tsx
import { useRouter } from "expo-router";
import React, { useState } from "react";
import { ActivityIndicator, Alert, Image, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { colors, fonts, theme } from "../constants/theme";
import { useAuth } from "./AuthContext";

export default function LoginScreen() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    if (!email || !password) {
      Alert.alert("Feil", "Skriv inn både e-post og passord");
      return;
    }

    setLoading(true);
    try {
      await login(email, password);
      router.replace("/events");
    } catch (err: any) {
      const message = err?.message ?? "Noe gikk galt";
      Alert.alert("Kunne ikke logge inn", message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <Image source={require("../assets/images/icon.png")} style={styles.logo} />
      <Text style={styles.title}>Hennings Alternativ Jul</Text>
      <TextInput
        style={styles.input}
        placeholder="E-post"
        placeholderTextColor={colors.ink400}
        autoCapitalize="none"
        keyboardType="email-address"
        autoComplete="email"
        value={email}
        onChangeText={setEmail}
      />
      <TextInput
        style={styles.input}
        placeholder="Passord"
        placeholderTextColor={colors.ink400}
        secureTextEntry
        autoComplete="password"
        value={password}
        onChangeText={setPassword}
      />
      <TouchableOpacity style={styles.button} onPress={handleLogin} disabled={loading}>
        {loading ? (
          <ActivityIndicator color={theme.primaryDark} />
        ) : (
          <Text style={styles.buttonText}>Logg inn</Text>
        )}
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24, backgroundColor: theme.background },
  logo: { width: 96, height: 96, borderRadius: 48, alignSelf: "center", marginBottom: 16 },
  title: {
    fontSize: 22,
    fontFamily: fonts.displayBold,
    marginBottom: 24,
    textAlign: "center",
    color: theme.primaryDark,
  },
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
});
