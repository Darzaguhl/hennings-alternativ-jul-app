import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import DateTimePicker, {
  DateTimePickerAndroid,
  DateTimePickerEvent,
} from "@react-native-community/datetimepicker";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "../../AuthContext";
import { useSettings } from "../../SettingsContext";
import OppgaverSection from "../../../components/OppgaverSection";
import PoolSection from "../../../components/PoolSection";
import CheckinSection from "../../../components/CheckinSection";

type CheckinMode = "personal_qr" | "event_qr";

interface EventDetail {
  id: number;
  title: string;
  description?: string;
  date?: string;
  code?: string;
  checkin_mode?: CheckinMode;
  created_by?: { id: number; username: string };
}

const CHECKIN_MODE_OPTIONS: { value: CheckinMode; label: string }[] = [
  { value: "event_qr", label: "Event QR (self check-in)" },
  { value: "personal_qr", label: "Personal QR (admin scans)" },
];

const combineDateAndTime = (current: Date | null, mode: "date" | "time", incoming: Date) => {
  if (mode === "date") {
    const next = new Date(incoming);
    if (current) next.setHours(current.getHours(), current.getMinutes(), 0, 0);
    return next;
  }
  const base = current ? new Date(current) : new Date();
  base.setHours(incoming.getHours(), incoming.getMinutes(), 0, 0);
  base.setSeconds(0, 0);
  return base;
};

/**
 * This app is dedicated to a single organization, so there's exactly one
 * event to manage rather than a browsable list. This screen fetches it
 * (the first/only result from /api/events/) and shows it directly. If none
 * exists yet, any signed-in user can create it once (first-time setup).
 */
export default function EventScreen() {
  const { apiFetch, currentUser } = useAuth();
  const { timeFormat, dateFormat } = useSettings();

  const [event, setEvent] = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showFormModal, setShowFormModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formTitle, setFormTitle] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formCheckinMode, setFormCheckinMode] = useState<CheckinMode>("event_qr");
  const [formDateTime, setFormDateTime] = useState<Date | null>(null);
  const [pickerMode, setPickerMode] = useState<"date" | "time" | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [iosPickerValue, setIosPickerValue] = useState(() => new Date());

  const loadEvent = useCallback(
    async ({ silent = false }: { silent?: boolean } = {}) => {
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      try {
        const response = await apiFetch("/api/events/");
        if (!response.ok) throw new Error(`Unable to load event (${response.status})`);
        const data: EventDetail[] = await response.json();
        setEvent(data[0] ?? null);
      } catch (err) {
        console.error("Error loading event", err);
        if (!silent) setError("Could not load the event.");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [apiFetch]
  );

  useEffect(() => {
    loadEvent();
  }, [loadEvent]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadEvent({ silent: true });
    setRefreshing(false);
  }, [loadEvent]);

  const formattedDate = useMemo(() => {
    if (!event?.date) return null;
    const parsed = new Date(event.date);
    if (Number.isNaN(parsed.getTime())) return null;
    const month = `${parsed.getMonth() + 1}`.padStart(2, "0");
    const day = `${parsed.getDate()}`.padStart(2, "0");
    const year = `${parsed.getFullYear()}`;
    const datePart = dateFormat === "MDY" ? `${month}/${day}/${year}` : `${day}/${month}/${year}`;
    const timePart = parsed.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: timeFormat === "12",
    });
    return `${datePart} ${timePart}`;
  }, [event?.date, dateFormat, timeFormat]);

  const formatDateOnly = useCallback(
    (value: Date | null) => {
      if (!value) return null;
      const month = `${value.getMonth() + 1}`.padStart(2, "0");
      const day = `${value.getDate()}`.padStart(2, "0");
      const year = `${value.getFullYear()}`;
      return dateFormat === "MDY" ? `${month}/${day}/${year}` : `${day}/${month}/${year}`;
    },
    [dateFormat]
  );

  const formatTimeOnly = useCallback(
    (value: Date | null) => {
      if (!value) return null;
      return value.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: timeFormat === "12" });
    },
    [timeFormat]
  );

  const openPicker = useCallback(
    (mode: "date" | "time") => {
      if (Platform.OS === "android") {
        DateTimePickerAndroid.open({
          value: formDateTime ?? new Date(),
          mode,
          is24Hour: timeFormat === "24",
          onChange: (_e, date) => {
            if (!date) return;
            setFormDateTime((prev) => combineDateAndTime(prev, mode, date));
          },
        });
        return;
      }
      setIosPickerValue(formDateTime ?? new Date());
      setPickerMode(mode);
      setShowPicker(true);
    },
    [formDateTime, timeFormat]
  );

  const openFormModal = useCallback(() => {
    if (event) {
      setFormTitle(event.title);
      setFormDescription(event.description ?? "");
      setFormCheckinMode(event.checkin_mode ?? "event_qr");
      setFormDateTime(event.date ? new Date(event.date) : null);
    } else {
      setFormTitle("");
      setFormDescription("");
      setFormCheckinMode("event_qr");
      setFormDateTime(null);
    }
    setShowFormModal(true);
  }, [event]);

  const closeFormModal = useCallback(() => {
    setShowFormModal(false);
    setShowPicker(false);
    setPickerMode(null);
    setSaving(false);
  }, []);

  const handleSave = useCallback(async () => {
    const title = formTitle.trim();
    if (!title) {
      Alert.alert("Missing title", "Please enter a title for the event.");
      return;
    }

    const payload: Record<string, unknown> = {
      title,
      description: formDescription.trim(),
      checkin_mode: formCheckinMode,
    };
    if (formDateTime) payload.date = formDateTime.toISOString();

    setSaving(true);
    try {
      const url = event ? `/api/events/${event.id}/` : "/api/events/";
      const method = event ? "PATCH" : "POST";
      const response = await apiFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Unable to save the event.");
      }
      const saved: EventDetail = await response.json();
      setEvent(saved);
      closeFormModal();
    } catch (err: any) {
      console.error("Error saving event", err);
      Alert.alert("Error", err?.message ?? "Failed to save the event.");
      setSaving(false);
    }
  }, [apiFetch, event, formTitle, formDescription, formCheckinMode, formDateTime, closeFormModal]);

  const handleDelete = useCallback(() => {
    if (!event) return;
    Alert.alert(
      "Delete Event",
      "Are you sure you want to delete this event? This removes all its oppgaver too.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: async () => {
            try {
              const response = await apiFetch(`/api/events/${event.id}/`, { method: "DELETE" });
              if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body?.detail ?? "Failed to delete event.");
              }
              setEvent(null);
            } catch (err: any) {
              console.error(err);
              Alert.alert("Error", err?.message ?? "Unable to delete event.");
            }
          },
        },
      ]
    );
  }, [apiFetch, event]);

  const isOwner = event?.created_by?.id === currentUser?.id;

  const formModal = (
    <Modal transparent animationType="slide" visible={showFormModal} onRequestClose={closeFormModal}>
      <View style={styles.modalOverlay}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.modalContainer}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>{event ? "Update event details" : "Create the event"}</Text>
            <ScrollView contentContainerStyle={styles.modalScroll}>
              <TextInput style={styles.input} placeholder="Title" value={formTitle} onChangeText={setFormTitle} />

              <Text style={styles.modalSectionHeading}>Date &amp; Time</Text>
              <View style={styles.selectorGroup}>
                <TouchableOpacity style={styles.selectorButton} onPress={() => openPicker("date")}>
                  <Text style={styles.selectorText}>{formatDateOnly(formDateTime) ?? "Select date"}</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.selectorButton} onPress={() => openPicker("time")}>
                  <Text style={styles.selectorText}>{formatTimeOnly(formDateTime) ?? "Select time (optional)"}</Text>
                </TouchableOpacity>
                {formDateTime && (
                  <TouchableOpacity style={styles.clearButton} onPress={() => setFormDateTime(null)}>
                    <Text style={styles.clearButtonText}>Clear date</Text>
                  </TouchableOpacity>
                )}
                {Platform.OS === "ios" && showPicker && pickerMode && (
                  <View style={styles.iosPickerWrapper}>
                    <DateTimePicker
                      value={iosPickerValue}
                      mode={pickerMode}
                      display="spinner"
                      themeVariant="dark"
                      textColor="#ffffff"
                      onChange={(evt: DateTimePickerEvent, date?: Date) => {
                        if (evt.type === "dismissed" || !date) return;
                        setIosPickerValue(date);
                      }}
                      style={styles.iosPicker}
                    />
                    <View style={styles.iosPickerActions}>
                      <TouchableOpacity
                        onPress={() => {
                          setFormDateTime((prev) => combineDateAndTime(prev, pickerMode, iosPickerValue));
                          setShowPicker(false);
                          setPickerMode(null);
                        }}
                      >
                        <Text style={styles.iosPickerActionText}>Done</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                )}
              </View>

              <Text style={styles.modalSectionHeading}>Check-in Mode</Text>
              <View style={styles.chipWrap}>
                {CHECKIN_MODE_OPTIONS.map((option) => (
                  <TouchableOpacity
                    key={option.value}
                    style={[styles.chip, formCheckinMode === option.value && styles.chipSelected]}
                    onPress={() => setFormCheckinMode(option.value)}
                  >
                    <Text style={[styles.chipText, formCheckinMode === option.value && styles.chipTextSelected]}>
                      {option.label}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>

              <Text style={styles.modalSectionHeading}>Description</Text>
              <TextInput
                style={[styles.input, styles.multilineInput]}
                multiline
                placeholder="Describe the event"
                value={formDescription}
                onChangeText={setFormDescription}
              />
            </ScrollView>

            <View style={styles.modalButtons}>
              <TouchableOpacity style={[styles.modalButton, styles.cancelButton]} onPress={closeFormModal} disabled={saving}>
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.modalButton, styles.saveButton]} onPress={handleSave} disabled={saving}>
                <Text style={styles.saveButtonText}>{saving ? "Saving…" : "Save"}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );

  if (loading) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#2e5339" />
          <Text style={styles.loadingText}>Loading…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (!event) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <Text style={styles.emptyTitle}>No event set up yet</Text>
          <Text style={styles.emptySubtitle}>Create the event to get started.</Text>
          <TouchableOpacity style={styles.addButton} onPress={openFormModal}>
            <Text style={styles.addButtonText}>+ Create event</Text>
          </TouchableOpacity>
        </View>
        {formModal}
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
      >
        <View style={styles.headerRow}>
          <Text style={styles.title}>{event.title}</Text>
          {isOwner && (
            <View style={styles.headerActions}>
              <TouchableOpacity style={styles.manageButton} onPress={openFormModal}>
                <Text style={styles.manageButtonText}>Edit</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.deleteButton} onPress={handleDelete}>
                <Text style={styles.deleteButtonText}>Delete</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
        {formattedDate && <Text style={styles.meta}>When: {formattedDate}</Text>}
        {event.description ? (
          <Text style={styles.description}>{event.description}</Text>
        ) : (
          <Text style={styles.placeholder}>No description provided for this event.</Text>
        )}

        <CheckinSection
          eventId={event.id}
          checkinMode={event.checkin_mode ?? "event_qr"}
          isOwner={isOwner}
          onResolved={() => loadEvent({ silent: true })}
        />

        <OppgaverSection eventId={event.id} isOwner={isOwner} />

        {isOwner && <PoolSection eventId={event.id} />}

        {event.code && <Text style={styles.codeHint}>ID: {event.code}</Text>}
      </ScrollView>

      {formModal}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  container: { padding: 20, gap: 16 },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20, gap: 12 },
  loadingText: { fontSize: 16, color: "#444" },
  errorText: { fontSize: 16, color: "#b00020", textAlign: "center" },
  emptyTitle: { fontSize: 20, fontWeight: "700", color: "#0f172a" },
  emptySubtitle: { fontSize: 14, color: "#64748b", textAlign: "center" },
  addButton: { backgroundColor: "#007AFF", paddingHorizontal: 18, paddingVertical: 10, borderRadius: 999, marginTop: 8 },
  addButtonText: { color: "white", fontWeight: "600" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  headerActions: { flexDirection: "row", gap: 8 },
  title: { fontSize: 26, fontWeight: "700", color: "#0f172a", flex: 1 },
  meta: { fontSize: 14, color: "#475569" },
  description: { fontSize: 15, color: "#1f2937" },
  placeholder: { fontSize: 14, color: "#94a3b8", fontStyle: "italic" },
  manageButton: { backgroundColor: "#e2e8f0", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 },
  manageButtonText: { color: "#334155", fontWeight: "600" },
  deleteButton: { backgroundColor: "#fee2e2", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 },
  deleteButtonText: { color: "#b91c1c", fontWeight: "600" },
  codeHint: { fontSize: 12, color: "#94a3b8", marginTop: 8 },
  modalOverlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "center", padding: 20 },
  modalContainer: { flex: 1, justifyContent: "center" },
  modalContent: { backgroundColor: "#fff", borderRadius: 16, padding: 20, gap: 16, maxHeight: "90%" },
  modalTitle: { fontSize: 20, fontWeight: "700", textAlign: "center" },
  modalScroll: { gap: 14 },
  modalSectionHeading: { fontSize: 15, fontWeight: "600", color: "#0f172a" },
  input: { borderWidth: 1, borderColor: "#d0d7e2", borderRadius: 8, padding: 12, fontSize: 16, backgroundColor: "#f8fafc", color: "#0f172a" },
  multilineInput: { minHeight: 80, textAlignVertical: "top" },
  selectorGroup: { gap: 10 },
  selectorButton: { borderWidth: 1, borderColor: "#d0d7e2", borderRadius: 10, paddingVertical: 10, paddingHorizontal: 14, backgroundColor: "#f8fafc" },
  selectorText: { color: "#0f172a", fontWeight: "500" },
  clearButton: { alignSelf: "flex-start" },
  clearButtonText: { color: "#ef4444", fontWeight: "600" },
  iosPickerWrapper: { width: "100%", backgroundColor: "#0f172a", borderRadius: 16, marginTop: 12, paddingVertical: 12, paddingHorizontal: 10, gap: 8, borderWidth: 1, borderColor: "rgba(37,99,235,0.35)" },
  iosPicker: { alignSelf: "stretch", backgroundColor: "transparent" },
  iosPickerActions: { alignItems: "flex-end" },
  iosPickerActionText: { color: "#bfdbfe", fontWeight: "600" },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", columnGap: 8, rowGap: 8 },
  chip: { borderRadius: 20, paddingVertical: 6, paddingHorizontal: 14, borderWidth: 1, borderColor: "#d0d7e2", backgroundColor: "#fff" },
  chipSelected: { backgroundColor: "#1d4ed8", borderColor: "#1d4ed8" },
  chipText: { color: "#1f2937", fontSize: 14, fontWeight: "500" },
  chipTextSelected: { color: "#f8fafc" },
  modalButtons: { flexDirection: "row", justifyContent: "flex-end", gap: 12 },
  modalButton: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 8 },
  cancelButton: { backgroundColor: "#e2e8f0" },
  cancelButtonText: { color: "#334155", fontWeight: "500" },
  saveButton: { backgroundColor: "#2563eb" },
  saveButtonText: { color: "white", fontWeight: "600" },
});
