import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import DateTimePicker, {
  DateTimePickerAndroid,
} from "@react-native-community/datetimepicker";
import { useAuth } from "../app/AuthContext";

type Criticality = "normal" | "critical";

interface UserSummary {
  id: number;
  username: string;
  email?: string;
}

export interface Shift {
  id: number;
  event: number;
  title: string;
  date: string;
  start_time: string;
  end_time: string;
  capacity: number | null;
  criticality: Criticality;
  is_critical: boolean;
  auto_approve: boolean;
  created_by: UserSummary;
  participants: UserSummary[];
  signup_count: number;
  assigned_count: number;
  is_full: boolean;
}

const pad = (value: number) => `${value}`.padStart(2, "0");
const toDateInput = (value: Date) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
const toTimeInput = (value: Date) => `${pad(value.getHours())}:${pad(value.getMinutes())}:00`;
const formatTimeRange = (shift: Shift) => `${shift.start_time.slice(0, 5)}–${shift.end_time.slice(0, 5)}`;

export default function OppgaverSection({ eventId, isOwner }: { eventId: number; isOwner: boolean }) {
  const { apiFetch, currentUser } = useAuth();
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyShiftId, setBusyShiftId] = useState<number | null>(null);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(() => new Date());
  const [startTime, setStartTime] = useState(() => new Date());
  const [endTime, setEndTime] = useState(() => new Date());
  const [capacity, setCapacity] = useState("");
  const [criticality, setCriticality] = useState<Criticality>("normal");
  const [activeField, setActiveField] = useState<"date" | "start" | "end" | null>(null);

  const [experiencePromptShift, setExperiencePromptShift] = useState<Shift | null>(null);
  const [hasExperience, setHasExperience] = useState(false);
  const [experienceNotes, setExperienceNotes] = useState("");

  const loadShifts = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiFetch(`/api/shifts/?event=${eventId}`);
      if (!response.ok) throw new Error(`Unable to load oppgaver (${response.status})`);
      const data: Shift[] = await response.json();
      setShifts(data);
    } catch (err) {
      console.error("Error loading shifts", err);
      Alert.alert("Error", "Could not load oppgaver right now.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, eventId]);

  useEffect(() => {
    loadShifts();
  }, [loadShifts]);

  const closeCreateModal = useCallback(() => {
    setShowCreateModal(false);
    setTitle("");
    setDate(new Date());
    setStartTime(new Date());
    setEndTime(new Date());
    setCapacity("");
    setCriticality("normal");
    setActiveField(null);
    setCreating(false);
  }, []);

  const openFieldPicker = useCallback(
    (field: "date" | "start" | "end") => {
      const current = field === "date" ? date : field === "start" ? startTime : endTime;
      const mode = field === "date" ? "date" : "time";

      if (Platform.OS === "android") {
        DateTimePickerAndroid.open({
          value: current,
          mode,
          onChange: (_event, picked) => {
            if (!picked) return;
            if (field === "date") setDate(picked);
            if (field === "start") setStartTime(picked);
            if (field === "end") setEndTime(picked);
          },
        });
        return;
      }
      setActiveField(field);
    },
    [date, startTime, endTime]
  );

  const handleCreateShift = useCallback(async () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      Alert.alert("Missing title", "Please enter a title for this oppgave.");
      return;
    }

    const payload: Record<string, unknown> = {
      event: eventId,
      title: trimmedTitle,
      date: toDateInput(date),
      start_time: toTimeInput(startTime),
      end_time: toTimeInput(endTime),
      criticality,
    };
    const parsedCapacity = capacity.trim() ? Number(capacity.trim()) : null;
    if (parsedCapacity !== null && !Number.isNaN(parsedCapacity)) {
      payload.capacity = parsedCapacity;
    }

    setCreating(true);
    try {
      const response = await apiFetch("/api/shifts/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Unable to create oppgave.");
      }
      const created: Shift = await response.json();
      setShifts((prev) => [...prev, created]);
      closeCreateModal();
    } catch (err: any) {
      console.error("Error creating shift", err);
      Alert.alert("Error", err?.message ?? "Failed to create oppgave.");
      setCreating(false);
    }
  }, [apiFetch, eventId, title, date, startTime, endTime, capacity, criticality, closeCreateModal]);

  const submitSignup = useCallback(
    async (shift: Shift, experience?: { has_relevant_experience: boolean; experience_notes: string }) => {
      setBusyShiftId(shift.id);
      try {
        const response = await apiFetch(`/api/shifts/${shift.id}/signup/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(experience ?? {}),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(body?.detail ?? "Unable to sign up for this oppgave.");
        }
        await loadShifts();
      } catch (err: any) {
        console.error("Error signing up", err);
        Alert.alert("Error", err?.message ?? "Failed to sign up.");
      } finally {
        setBusyShiftId(null);
      }
    },
    [apiFetch, loadShifts]
  );

  const handleSignupPress = useCallback(
    (shift: Shift) => {
      if (shift.is_critical) {
        setHasExperience(false);
        setExperienceNotes("");
        setExperiencePromptShift(shift);
        return;
      }
      submitSignup(shift);
    },
    [submitSignup]
  );

  const confirmExperienceAndSignup = useCallback(() => {
    if (!experiencePromptShift) return;
    const shift = experiencePromptShift;
    setExperiencePromptShift(null);
    submitSignup(shift, { has_relevant_experience: hasExperience, experience_notes: experienceNotes.trim() });
  }, [experiencePromptShift, hasExperience, experienceNotes, submitSignup]);

  const handleWithdraw = useCallback(
    async (shift: Shift) => {
      setBusyShiftId(shift.id);
      try {
        const response = await apiFetch(`/api/shifts/${shift.id}/withdraw/`, { method: "POST" });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(body?.detail ?? "Unable to withdraw.");
        }
        await loadShifts();
      } catch (err: any) {
        console.error("Error withdrawing", err);
        Alert.alert("Error", err?.message ?? "Failed to withdraw.");
      } finally {
        setBusyShiftId(null);
      }
    },
    [apiFetch, loadShifts]
  );

  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Oppgaver</Text>
        {isOwner && (
          <TouchableOpacity style={styles.addButton} onPress={() => setShowCreateModal(true)}>
            <Text style={styles.addButtonText}>+ Add oppgave</Text>
          </TouchableOpacity>
        )}
      </View>

      {loading ? (
        <ActivityIndicator />
      ) : shifts.length ? (
        shifts.map((shift) => {
          const signedUp = shift.participants.some((p) => p.id === currentUser?.id);
          const busy = busyShiftId === shift.id;
          return (
            <View key={shift.id} style={styles.card}>
              <View style={styles.cardHeaderRow}>
                <Text style={styles.cardTitle}>{shift.title}</Text>
                {shift.is_critical && (
                  <View style={styles.criticalBadge}>
                    <Text style={styles.criticalBadgeText}>Critical</Text>
                  </View>
                )}
              </View>
              <Text style={styles.cardMeta}>
                {shift.date} · {formatTimeRange(shift)}
              </Text>
              <Text style={styles.cardMeta}>
                {shift.assigned_count} assigned
                {shift.capacity !== null ? ` / ${shift.capacity} capacity` : ""} ·{" "}
                {shift.signup_count} signed up
              </Text>

              {signedUp ? (
                <TouchableOpacity
                  style={[styles.withdrawButton, busy && styles.buttonDisabled]}
                  onPress={() => handleWithdraw(shift)}
                  disabled={busy}
                >
                  <Text style={styles.withdrawButtonText}>{busy ? "Working…" : "Withdraw"}</Text>
                </TouchableOpacity>
              ) : (
                <TouchableOpacity
                  style={[
                    styles.signupButton,
                    (shift.is_full || busy) && styles.buttonDisabled,
                  ]}
                  onPress={() => handleSignupPress(shift)}
                  disabled={shift.is_full || busy}
                >
                  <Text style={styles.signupButtonText}>
                    {shift.is_full ? "Full" : busy ? "Working…" : "Sign up"}
                  </Text>
                </TouchableOpacity>
              )}
            </View>
          );
        })
      ) : (
        <Text style={styles.placeholder}>No oppgaver added for this event yet.</Text>
      )}

      <Modal transparent animationType="slide" visible={!!experiencePromptShift} onRequestClose={() => setExperiencePromptShift(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>{experiencePromptShift?.title}</Text>
            <Text style={styles.modalBody}>
              This is a critical oppgave. Do you have relevant previous experience or education?
            </Text>
            <View style={styles.switchRow}>
              <Text style={styles.switchLabel}>I have relevant experience</Text>
              <Switch value={hasExperience} onValueChange={setHasExperience} />
            </View>
            <TextInput
              style={[styles.input, styles.multilineInput]}
              placeholder="Briefly describe your experience (optional)"
              value={experienceNotes}
              onChangeText={setExperienceNotes}
              multiline
            />
            <View style={styles.modalButtons}>
              <TouchableOpacity
                style={[styles.modalButton, styles.cancelButton]}
                onPress={() => setExperiencePromptShift(null)}
              >
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.modalButton, styles.saveButton]}
                onPress={confirmExperienceAndSignup}
              >
                <Text style={styles.saveButtonText}>Sign up</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      <Modal transparent animationType="slide" visible={showCreateModal} onRequestClose={closeCreateModal}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Add Oppgave</Text>
            <ScrollView contentContainerStyle={styles.modalScroll} keyboardShouldPersistTaps="handled">
              <TextInput style={styles.input} placeholder="Title (e.g. Kjøkken)" value={title} onChangeText={setTitle} />

              <TouchableOpacity style={styles.selectorButton} onPress={() => openFieldPicker("date")}>
                <Text style={styles.selectorText}>Date: {toDateInput(date)}</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.selectorButton} onPress={() => openFieldPicker("start")}>
                <Text style={styles.selectorText}>Start: {toTimeInput(startTime).slice(0, 5)}</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.selectorButton} onPress={() => openFieldPicker("end")}>
                <Text style={styles.selectorText}>End: {toTimeInput(endTime).slice(0, 5)}</Text>
              </TouchableOpacity>

              {Platform.OS === "ios" && activeField && (
                <View style={styles.iosPickerWrapper}>
                  <DateTimePicker
                    value={activeField === "date" ? date : activeField === "start" ? startTime : endTime}
                    mode={activeField === "date" ? "date" : "time"}
                    display="spinner"
                    onChange={(_event, picked) => {
                      if (!picked) return;
                      if (activeField === "date") setDate(picked);
                      if (activeField === "start") setStartTime(picked);
                      if (activeField === "end") setEndTime(picked);
                    }}
                  />
                  <TouchableOpacity onPress={() => setActiveField(null)}>
                    <Text style={styles.iosPickerDoneText}>Done</Text>
                  </TouchableOpacity>
                </View>
              )}

              <TextInput
                style={styles.input}
                placeholder="Capacity (optional)"
                value={capacity}
                onChangeText={setCapacity}
                keyboardType="number-pad"
              />

              <Text style={styles.sectionHeading}>Criticality</Text>
              <View style={styles.chipWrap}>
                {(["normal", "critical"] as const).map((option) => (
                  <TouchableOpacity
                    key={option}
                    style={[styles.chip, criticality === option && styles.chipSelected]}
                    onPress={() => setCriticality(option)}
                  >
                    <Text style={[styles.chipText, criticality === option && styles.chipTextSelected]}>
                      {option === "critical" ? "Critical (needs experience)" : "Normal"}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
            </ScrollView>
            <View style={styles.modalButtons}>
              <TouchableOpacity style={[styles.modalButton, styles.cancelButton]} onPress={closeCreateModal}>
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.modalButton, styles.saveButton, creating && styles.buttonDisabled]}
                onPress={handleCreateShift}
                disabled={creating}
              >
                <Text style={styles.saveButtonText}>{creating ? "Saving…" : "Save"}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 12, marginBottom: 20 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  sectionTitle: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  addButton: { backgroundColor: "#007AFF", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 },
  addButtonText: { color: "white", fontWeight: "600", fontSize: 13 },
  placeholder: { color: "#64748b", fontSize: 14 },
  card: { backgroundColor: "#f8fafc", borderRadius: 12, padding: 14, gap: 6, borderWidth: 1, borderColor: "#e2e8f0" },
  cardHeaderRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  cardTitle: { fontSize: 16, fontWeight: "600", color: "#0f172a" },
  cardMeta: { fontSize: 13, color: "#475569" },
  criticalBadge: { backgroundColor: "#fee2e2", paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999 },
  criticalBadgeText: { color: "#b91c1c", fontSize: 12, fontWeight: "700" },
  signupButton: { backgroundColor: "#2563eb", paddingVertical: 8, borderRadius: 8, alignItems: "center", marginTop: 4 },
  signupButtonText: { color: "white", fontWeight: "600" },
  withdrawButton: { backgroundColor: "#e2e8f0", paddingVertical: 8, borderRadius: 8, alignItems: "center", marginTop: 4 },
  withdrawButtonText: { color: "#334155", fontWeight: "600" },
  buttonDisabled: { opacity: 0.5 },
  modalOverlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "center", padding: 20 },
  modalContent: { backgroundColor: "#fff", borderRadius: 16, padding: 20, gap: 14, maxHeight: "90%" },
  modalTitle: { fontSize: 20, fontWeight: "700", textAlign: "center" },
  modalBody: { fontSize: 14, color: "#334155" },
  modalScroll: { gap: 12 },
  input: { borderWidth: 1, borderColor: "#d0d7e2", borderRadius: 8, padding: 12, fontSize: 16, backgroundColor: "#f8fafc", color: "#0f172a" },
  multilineInput: { minHeight: 70, textAlignVertical: "top" },
  selectorButton: { borderWidth: 1, borderColor: "#d0d7e2", borderRadius: 10, paddingVertical: 10, paddingHorizontal: 14, backgroundColor: "#f8fafc" },
  selectorText: { color: "#0f172a", fontWeight: "500" },
  iosPickerWrapper: { backgroundColor: "#0f172a", borderRadius: 16, padding: 10, gap: 8, alignItems: "flex-end" },
  iosPickerDoneText: { color: "#bfdbfe", fontWeight: "600", paddingHorizontal: 8 },
  sectionHeading: { fontSize: 15, fontWeight: "600", color: "#0f172a" },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", columnGap: 8, rowGap: 8 },
  chip: { borderRadius: 20, paddingVertical: 6, paddingHorizontal: 14, borderWidth: 1, borderColor: "#d0d7e2", backgroundColor: "#fff" },
  chipSelected: { backgroundColor: "#1d4ed8", borderColor: "#1d4ed8" },
  chipText: { color: "#1f2937", fontSize: 14, fontWeight: "500" },
  chipTextSelected: { color: "#f8fafc" },
  switchRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  switchLabel: { fontSize: 14, color: "#0f172a", flex: 1, marginRight: 12 },
  modalButtons: { flexDirection: "row", justifyContent: "flex-end", gap: 12 },
  modalButton: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 8 },
  cancelButton: { backgroundColor: "#e2e8f0" },
  cancelButtonText: { color: "#334155", fontWeight: "500" },
  saveButton: { backgroundColor: "#2563eb" },
  saveButtonText: { color: "white", fontWeight: "600" },
});
