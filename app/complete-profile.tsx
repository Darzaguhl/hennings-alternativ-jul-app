// app/complete-profile.tsx
import { useRouter } from "expo-router";
import React, { useEffect, useState } from "react";
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
} from "react-native";
import { colors, fonts, theme } from "../constants/theme";
import { useAuth } from "./AuthContext";

const BIRTHDATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

// Shown once after someone's first Google/Microsoft sign-in: that creates an
// account with only a name and e-mail, but the organisation needs phone,
// address and birthdate on file for every volunteer (the website's signup
// form collects the same). Saved via the same PATCH the website uses.
export default function CompleteProfileScreen() {
  const { currentUser, apiFetch, refreshCurrentUser } = useAuth();
  const router = useRouter();
  const [firstName, setFirstName] = useState(currentUser?.first_name ?? "");
  const [lastName, setLastName] = useState(currentUser?.last_name ?? "");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");
  const [birthdate, setBirthdate] = useState("");
  const [saving, setSaving] = useState(false);

  // The user record can arrive just after this screen mounts; fill in the
  // names from the provider then, without overwriting anything already typed.
  useEffect(() => {
    if (!currentUser) return;
    setFirstName((prev) => prev || currentUser.first_name);
    setLastName((prev) => prev || currentUser.last_name);
  }, [currentUser]);

  const handleSave = async () => {
    if (!currentUser) return;
    if (!firstName.trim() || !lastName.trim() || !phone.trim() || !address.trim() || !birthdate.trim()) {
      Alert.alert("Mangler opplysninger", "Fyll ut alle feltene.");
      return;
    }
    if (!BIRTHDATE_PATTERN.test(birthdate.trim())) {
      Alert.alert("Fødselsdato", "Skriv fødselsdatoen som ÅÅÅÅ-MM-DD, for eksempel 1990-05-15.");
      return;
    }

    setSaving(true);
    try {
      const response = await apiFetch(`/api/users/${currentUser.id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          first_name: firstName.trim(),
          last_name: lastName.trim(),
          phone: phone.trim(),
          address: address.trim(),
          birthdate: birthdate.trim(),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const fieldError = ["first_name", "last_name", "phone", "address", "birthdate"]
          .map((field) => body?.[field]?.[0])
          .find(Boolean);
        throw new Error(fieldError ?? body?.detail ?? "Kunne ikke lagre opplysningene dine.");
      }
      await refreshCurrentUser();
      router.replace("/events");
    } catch (err: any) {
      Alert.alert("Kunne ikke lagre", err?.message ?? "Noe gikk galt");
    } finally {
      setSaving(false);
    }
  };

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Fullfør profilen din</Text>
        <Text style={styles.intro}>
          Vi trenger noen opplysninger om deg før du kan melde deg på vakter. Kun admin og de som leder vaktene ser dem.
        </Text>

        <TextInput style={styles.input} placeholder="Fornavn" placeholderTextColor={colors.ink400} value={firstName} onChangeText={setFirstName} autoComplete="given-name" />
        <TextInput style={styles.input} placeholder="Etternavn" placeholderTextColor={colors.ink400} value={lastName} onChangeText={setLastName} autoComplete="family-name" />
        <TextInput style={styles.input} placeholder="Telefon" placeholderTextColor={colors.ink400} value={phone} onChangeText={setPhone} keyboardType="phone-pad" autoComplete="tel" />
        <TextInput style={styles.input} placeholder="Adresse" placeholderTextColor={colors.ink400} value={address} onChangeText={setAddress} autoComplete="street-address" />
        <TextInput
          style={styles.input}
          placeholder="Fødselsdato (ÅÅÅÅ-MM-DD)"
          placeholderTextColor={colors.ink400}
          value={birthdate}
          onChangeText={setBirthdate}
          keyboardType="numbers-and-punctuation"
          maxLength={10}
        />

        <TouchableOpacity style={styles.button} onPress={handleSave} disabled={saving}>
          {saving ? <ActivityIndicator color={theme.primaryDark} /> : <Text style={styles.buttonText}>Lagre</Text>}
        </TouchableOpacity>
        <TouchableOpacity onPress={() => router.replace("/events")} style={styles.skip}>
          <Text style={styles.skipText}>Hopp over nå</Text>
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: theme.background },
  container: { flexGrow: 1, justifyContent: "center", padding: 24 },
  title: { fontSize: 22, fontFamily: fonts.displayBold, marginBottom: 8, textAlign: "center", color: theme.primaryDark },
  intro: { fontFamily: fonts.body, fontSize: 14, color: theme.text, textAlign: "center", marginBottom: 20 },
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
  button: { backgroundColor: theme.accent, paddingVertical: 14, borderRadius: 999, alignItems: "center", marginTop: 8 },
  buttonText: { fontFamily: fonts.bodySemiBold, fontSize: 16, color: theme.primaryDark },
  skip: { marginTop: 20, alignItems: "center" },
  skipText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: theme.primary },
});
