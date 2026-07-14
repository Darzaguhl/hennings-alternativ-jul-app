import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { theme } from "../constants/theme";
import { useAuth } from "../app/AuthContext";

interface Skill {
  id: number;
  name: string;
}

interface Profile {
  id: number;
  skills: Skill[];
  experience_notes: string;
}

export default function OppgaverSection() {
  const { apiFetch } = useAuth();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<number[]>([]);
  const [experienceNotes, setExperienceNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      const [meRes, skillsRes] = await Promise.all([apiFetch("/api/users/me/"), apiFetch("/api/skills/")]);
      if (!meRes.ok) throw new Error("Unable to load profile.");
      if (!skillsRes.ok) throw new Error("Unable to load oppgaver.");
      const me: Profile = await meRes.json();
      const skills: Skill[] = await skillsRes.json();
      setProfile(me);
      setAllSkills(skills);
      setSelectedSkillIds(me.skills.map((s) => s.id));
      setExperienceNotes(me.experience_notes ?? "");
    } catch (err) {
      console.error("Error loading oppgaver", err);
      Alert.alert("Error", "Could not load oppgaver right now.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const toggleSkill = useCallback((id: number) => {
    setSelectedSkillIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
  }, []);

  const handleSave = useCallback(async () => {
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
        throw new Error(body?.detail ?? "Unable to save oppgaver.");
      }
      const updated: Profile = await response.json();
      setProfile(updated);
    } catch (err: any) {
      console.error("Error saving oppgaver", err);
      Alert.alert("Error", err?.message ?? "Failed to save oppgaver.");
    } finally {
      setSaving(false);
    }
  }, [apiFetch, profile, selectedSkillIds, experienceNotes]);

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>Oppgaver</Text>
      <Text style={styles.helperText}>
        What you can help with — visible to event admins when they&apos;re deciding which vakt fits you best.
      </Text>
      {loading ? (
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
            style={[styles.saveButton, saving && styles.buttonDisabled]}
            onPress={handleSave}
            disabled={saving}
          >
            <Text style={styles.saveButtonText}>{saving ? "Saving…" : "Save"}</Text>
          </TouchableOpacity>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 10, marginBottom: 20 },
  sectionTitle: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  helperText: { fontSize: 13, color: "#64748b" },
  optionRow: { flexDirection: "row", gap: 10, flexWrap: "wrap" },
  optionButton: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#d0d7e2",
    backgroundColor: "#f8fafc",
  },
  optionButtonActive: { backgroundColor: theme.primary, borderColor: theme.primary },
  optionText: { color: "#0f172a", fontWeight: "500", fontSize: 13 },
  optionTextActive: { color: "#ffffff" },
  textArea: {
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderRadius: 8,
    padding: 12,
    fontSize: 14,
    minHeight: 70,
    textAlignVertical: "top",
    backgroundColor: "#f8fafc",
    color: "#0f172a",
  },
  saveButton: { backgroundColor: theme.accent, paddingVertical: 10, borderRadius: 8, alignItems: "center", alignSelf: "flex-start", paddingHorizontal: 20 },
  buttonDisabled: { opacity: 0.5 },
  saveButtonText: { color: theme.primaryDark, fontWeight: "600" },
});
