import { useRouter } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { colors, theme } from "../../constants/theme";
import { useAuth } from "../AuthContext";
import { useSettings } from "../SettingsContext";

export default function ProfileScreen() {
  const { logout, currentUser, apiFetch, refreshCurrentUser } = useAuth();
  const router = useRouter();
  const { timeFormat, dateFormat, setTimeFormat, setDateFormat, loading } = useSettings();

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [nameError, setNameError] = useState("");
  const [nameSaved, setNameSaved] = useState(false);

  useEffect(() => {
    setFirstName(currentUser?.first_name ?? "");
    setLastName(currentUser?.last_name ?? "");
  }, [currentUser]);

  const handleLogout = async () => {
    await logout();
    router.replace("/login");
  };

  const saveName = async () => {
    if (!currentUser) return;
    setSavingName(true);
    setNameError("");
    setNameSaved(false);
    try {
      const response = await apiFetch(`/api/users/${currentUser.id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ first_name: firstName.trim(), last_name: lastName.trim() }),
      });
      if (!response.ok) {
        throw new Error("Could not save your name");
      }
      await refreshCurrentUser();
      setNameSaved(true);
    } catch {
      setNameError("Could not save your name. Please try again.");
    } finally {
      setSavingName(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.headerRow}>
        <Text style={styles.heading}>Profile</Text>
        <TouchableOpacity style={styles.logoutButton} onPress={handleLogout}>
          <Text style={styles.logoutButtonText}>Logout</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Name</Text>
        <Text style={styles.sectionHint}>
          Lets admins recognize you on the volunteer list instead of just your email.
        </Text>
        <View style={styles.nameRow}>
          <TextInput
            style={styles.nameInput}
            value={firstName}
            onChangeText={(text) => {
              setFirstName(text);
              setNameSaved(false);
            }}
            placeholder="First name"
            autoCapitalize="words"
          />
          <TextInput
            style={styles.nameInput}
            value={lastName}
            onChangeText={(text) => {
              setLastName(text);
              setNameSaved(false);
            }}
            placeholder="Last name"
            autoCapitalize="words"
          />
        </View>
        {nameError ? <Text style={styles.nameError}>{nameError}</Text> : null}
        {nameSaved ? <Text style={styles.nameSaved}>Saved.</Text> : null}
        <TouchableOpacity
          style={[styles.saveButton, savingName && styles.saveButtonDisabled]}
          onPress={saveName}
          disabled={savingName}
        >
          {savingName ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Text style={styles.saveButtonText}>Save name</Text>
          )}
        </TouchableOpacity>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Time Format</Text>
        {loading ? (
          <ActivityIndicator />
        ) : (
          <View style={styles.optionRow}>
            {([
              { label: "12-hour", value: "12" },
              { label: "24-hour", value: "24" },
            ] as const).map((option) => (
              <TouchableOpacity
                key={option.value}
                style={[styles.optionButton, timeFormat === option.value && styles.optionButtonActive]}
                onPress={() => setTimeFormat(option.value)}
                disabled={timeFormat === option.value}
              >
                <Text
                  style={[styles.optionText, timeFormat === option.value && styles.optionTextActive]}
                >
                  {option.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        )}
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Date Format</Text>
        {loading ? (
          <ActivityIndicator />
        ) : (
          <View style={styles.optionRow}>
            {([
              { label: "Month / Day / Year", value: "MDY" },
              { label: "Day / Month / Year", value: "DMY" },
            ] as const).map((option) => (
              <TouchableOpacity
                key={option.value}
                style={[styles.optionButton, dateFormat === option.value && styles.optionButtonActive]}
                onPress={() => setDateFormat(option.value)}
                disabled={dateFormat === option.value}
              >
                <Text
                  style={[styles.optionText, dateFormat === option.value && styles.optionTextActive]}
                >
                  {option.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, padding: 24, gap: 24 },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  heading: { fontSize: 28, fontWeight: "700" },
  logoutButton: {
    backgroundColor: "#fee2e2",
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
  },
  logoutButtonText: { color: "#b91c1c", fontWeight: "600" },
  section: { gap: 12 },
  sectionTitle: { fontSize: 18, fontWeight: "600" },
  optionRow: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
  optionButton: {
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#d0d7e2",
    backgroundColor: "#f8fafc",
  },
  optionButtonActive: {
    backgroundColor: theme.primary,
    borderColor: theme.primary,
  },
  optionText: { color: "#0f172a", fontWeight: "500" },
  optionTextActive: { color: "#ffffff" },
  sectionHint: { fontSize: 13, color: "#64748b" },
  nameRow: { flexDirection: "row", gap: 12 },
  nameInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 15,
    backgroundColor: "#f8fafc",
  },
  nameError: { color: "#b91c1c", fontSize: 13 },
  nameSaved: { color: colors.green700, fontSize: 13 },
  saveButton: {
    backgroundColor: theme.primary,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
  },
  saveButtonDisabled: { opacity: 0.6 },
  saveButtonText: { color: colors.white, fontWeight: "600" },
});
