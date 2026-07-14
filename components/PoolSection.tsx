import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { theme } from "../constants/theme";
import { useAuth } from "../app/AuthContext";
import type { Shift } from "./OppgaverSection";

interface UserSummary {
  id: number;
  username: string;
  email?: string;
  skills?: { id: number; name: string }[];
  experience_notes?: string;
}

interface CandidateSignup {
  id: number;
  shift: Shift;
  user: UserSummary;
  has_relevant_experience: boolean | null;
  experience_notes: string;
  created_at: string;
}

interface PoolEntry {
  user: UserSummary;
  checked_in_at: string;
  candidates: CandidateSignup[];
  suggested_shift: Shift | null;
}

const formatTimeRange = (shift: Shift) => `${shift.start_time.slice(0, 5)}–${shift.end_time.slice(0, 5)}`;

export default function PoolSection({ eventId }: { eventId: number }) {
  const { apiFetch } = useAuth();
  const [entries, setEntries] = useState<PoolEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const loadPool = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiFetch(`/api/events/${eventId}/pool/`);
      if (!response.ok) throw new Error(`Unable to load the pool (${response.status})`);
      const data: PoolEntry[] = await response.json();
      setEntries(data);
    } catch (err) {
      console.error("Error loading pool", err);
      Alert.alert("Error", "Could not load the check-in pool right now.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, eventId]);

  useEffect(() => {
    loadPool();
  }, [loadPool]);

  const handleAssign = useCallback(
    async (userId: number, shiftId: number) => {
      const key = `${userId}-${shiftId}`;
      setBusyKey(key);
      try {
        const response = await apiFetch(`/api/events/${eventId}/assign/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: userId, shift_id: shiftId }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(body?.detail ?? "Unable to assign this oppgave.");
        }
        await loadPool();
      } catch (err: any) {
        console.error("Error assigning", err);
        Alert.alert("Error", err?.message ?? "Failed to assign.");
      } finally {
        setBusyKey(null);
      }
    },
    [apiFetch, eventId, loadPool]
  );

  if (loading) {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Pool</Text>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Pool</Text>
        <TouchableOpacity onPress={loadPool}>
          <Text style={styles.refreshText}>Refresh</Text>
        </TouchableOpacity>
      </View>

      {entries.length ? (
        entries.map((entry) => (
          <View key={entry.user.id} style={styles.card}>
            <Text style={styles.userName}>{entry.user.username}</Text>
            {entry.user.experience_notes ? (
              <Text style={styles.experienceNotes}>{entry.user.experience_notes}</Text>
            ) : null}
            {entry.user.skills?.length ? (
              <Text style={styles.skills}>Skills: {entry.user.skills.map((s) => s.name).join(", ")}</Text>
            ) : null}

            {entry.candidates.length === 0 ? (
              <Text style={styles.placeholder}>Not signed up for any oppgave today.</Text>
            ) : (
              entry.candidates.map((candidate) => {
                const shift = candidate.shift;
                const isSuggested = entry.suggested_shift?.id === shift.id;
                const key = `${entry.user.id}-${shift.id}`;
                const busy = busyKey === key;
                return (
                  <View
                    key={shift.id}
                    style={[styles.candidateRow, isSuggested && styles.candidateRowSuggested]}
                  >
                    <View style={styles.candidateInfo}>
                      <View style={styles.candidateTitleRow}>
                        <Text style={styles.candidateTitle}>{shift.title}</Text>
                        {isSuggested && (
                          <View style={styles.suggestedBadge}>
                            <Text style={styles.suggestedBadgeText}>Suggested</Text>
                          </View>
                        )}
                        {shift.is_critical && (
                          <View style={styles.criticalBadge}>
                            <Text style={styles.criticalBadgeText}>Critical</Text>
                          </View>
                        )}
                      </View>
                      <Text style={styles.candidateMeta}>
                        {shift.date} · {formatTimeRange(shift)}
                      </Text>
                      {shift.is_critical && (
                        <Text style={styles.candidateMeta}>
                          Experience:{" "}
                          {candidate.has_relevant_experience === true
                            ? "Confirmed yes"
                            : candidate.has_relevant_experience === false
                            ? "Said no"
                            : "Not answered"}
                          {candidate.experience_notes ? ` — "${candidate.experience_notes}"` : ""}
                        </Text>
                      )}
                    </View>
                    <TouchableOpacity
                      style={[styles.assignButton, busy && styles.buttonDisabled]}
                      onPress={() => handleAssign(entry.user.id, shift.id)}
                      disabled={busy}
                    >
                      <Text style={styles.assignButtonText}>{busy ? "…" : "Assign"}</Text>
                    </TouchableOpacity>
                  </View>
                );
              })
            )}
          </View>
        ))
      ) : (
        <Text style={styles.placeholder}>Nobody is waiting in the pool right now.</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 12, marginBottom: 20 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  sectionTitle: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  refreshText: { color: theme.primary, fontWeight: "600" },
  placeholder: { color: "#64748b", fontSize: 14 },
  card: { backgroundColor: "#f8fafc", borderRadius: 12, padding: 14, gap: 8, borderWidth: 1, borderColor: "#e2e8f0" },
  userName: { fontSize: 16, fontWeight: "700", color: "#0f172a" },
  experienceNotes: { fontSize: 13, color: "#334155", fontStyle: "italic" },
  skills: { fontSize: 13, color: "#334155" },
  candidateRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#fff",
    borderRadius: 10,
    padding: 10,
    borderWidth: 1,
    borderColor: "#e2e8f0",
    gap: 10,
  },
  candidateRowSuggested: { borderColor: "#22c55e", backgroundColor: "#f0fdf4" },
  candidateInfo: { flex: 1, gap: 2 },
  candidateTitleRow: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
  candidateTitle: { fontSize: 14, fontWeight: "600", color: "#0f172a" },
  candidateMeta: { fontSize: 12, color: "#475569" },
  suggestedBadge: { backgroundColor: "#dcfce7", paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999 },
  suggestedBadgeText: { color: "#166534", fontSize: 11, fontWeight: "700" },
  criticalBadge: { backgroundColor: "#fee2e2", paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999 },
  criticalBadgeText: { color: "#b91c1c", fontSize: 11, fontWeight: "700" },
  assignButton: { backgroundColor: theme.accent, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
  assignButtonText: { color: theme.primaryDark, fontWeight: "600" },
  buttonDisabled: { opacity: 0.5 },
});
