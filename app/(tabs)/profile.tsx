import { useRouter } from "expo-router";
import React from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { theme } from "../../constants/theme";
import { useAuth } from "../AuthContext";
import { useSettings } from "../SettingsContext";

export default function ProfileScreen() {
  const { logout } = useAuth();
  const router = useRouter();
  const { timeFormat, dateFormat, setTimeFormat, setDateFormat, loading } = useSettings();

  const handleLogout = async () => {
    await logout();
    router.replace("/login");
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
});
