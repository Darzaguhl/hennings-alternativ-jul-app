import { useRouter } from "expo-router";
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { theme } from "../../constants/theme";
import { useAuth } from "../AuthContext";
import { useSettings } from "../SettingsContext";

interface Skill {
  id: number;
  name: string;
}

interface Profile {
  id: number;
  username: string;
  email?: string;
  skills: Skill[];
  experience_notes: string;
}

export default function ProfileScreen() {
  const { logout, apiFetch } = useAuth();
  const router = useRouter();
  const { timeFormat, dateFormat, setTimeFormat, setDateFormat, loading } = useSettings();

  const [profile, setProfile] = useState<Profile | null>(null);
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<number[]>([]);
  const [experienceNotes, setExperienceNotes] = useState("");
  const [profileLoading, setProfileLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadProfile = useCallback(async () => {
    setProfileLoading(true);
    try {
      const [meRes, skillsRes] = await Promise.all([
        apiFetch("/api/users/me/"),
        apiFetch("/api/skills/"),
      ]);
      if (!meRes.ok) throw new Error("Unable to load profile.");
      if (!skillsRes.ok) throw new Error("Unable to load skills.");
      const me: Profile = await meRes.json();
      const skills: Skill[] = await skillsRes.json();
      setProfile(me);
      setAllSkills(skills);
      setSelectedSkillIds(me.skills.map((s) => s.id));
      setExperienceNotes(me.experience_notes ?? "");
    } catch (err) {
      console.error("Error loading profile", err);
      Alert.alert("Error", "Could not load your profile right now.");
    } finally {
      setProfileLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const toggleSkill = useCallback((id: number) => {
    setSelectedSkillIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
  }, []);

  const handleSaveProfile = useCallback(async () => {
    if (!profile) return;
    setSaving(true);
    try {
      const response = await apiFetch(`/api/users/${profile.id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_ids: selectedSkillIds, experience_notes: experienceNotes }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Unable to save profile.");
      }
      const updated: Profile = await response.json();
      setProfile(updated);
      Alert.alert("Saved", "Your skills and experience were updated.");
    } catch (err: any) {
      console.error("Error saving profile", err);
      Alert.alert("Error", err?.message ?? "Failed to save profile.");
    } finally {
      setSaving(false);
    }
  }, [apiFetch, profile, selectedSkillIds, experienceNotes]);

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
        <Text style={styles.sectionTitle}>Skills &amp; Experience</Text>
        <Text style={styles.helperText}>
          Visible to event admins when they&apos;re deciding which oppgave fits you best.
        </Text>
        {profileLoading ? (
          <ActivityIndicator />
        ) : (
          <>
            <View style={styles.optionRow}>
              {allSkills.map((skill) => {
                const selected = selectedSkillIds.includes(skill.id);
                return (
                  <TouchableOpacity
                    key={skill.id}
                    style={[styles.optionButton, selected && styles.optionButtonActive]}
                    onPress={() => toggleSkill(skill.id)}
                  >
                    <Text style={[styles.optionText, selected && styles.optionTextActive]}>{skill.name}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            <TextInput
              style={styles.textArea}
              placeholder="Previous experience, education, certifications…"
              value={experienceNotes}
              onChangeText={setExperienceNotes}
              multiline
            />
            <TouchableOpacity
              style={[styles.saveButton, saving && styles.saveButtonDisabled]}
              onPress={handleSaveProfile}
              disabled={saving}
            >
              <Text style={styles.saveButtonText}>{saving ? "Saving…" : "Save"}</Text>
            </TouchableOpacity>
          </>
        )}
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
  helperText: { fontSize: 13, color: "#64748b" },
  textArea: {
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderRadius: 8,
    padding: 12,
    fontSize: 15,
    minHeight: 80,
    textAlignVertical: "top",
    backgroundColor: "#f8fafc",
    color: "#0f172a",
  },
  saveButton: { backgroundColor: theme.accent, paddingVertical: 10, borderRadius: 8, alignItems: "center" },
  saveButtonDisabled: { opacity: 0.5 },
  saveButtonText: { color: theme.primaryDark, fontWeight: "600" },
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
