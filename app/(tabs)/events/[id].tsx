import { useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CameraType, CameraView, useCameraPermissions } from "expo-camera";
import {
  ActivityIndicator,
  Alert,
  Keyboard,
  KeyboardAvoidingView,
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
  DateTimePickerEvent,
} from "@react-native-community/datetimepicker";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "../../AuthContext";
import { useSettings } from "../../SettingsContext";
import OppgaverSection from "../../../components/OppgaverSection";
import PoolSection from "../../../components/PoolSection";
import CheckinSection from "../../../components/CheckinSection";

interface UserDetail {
  id: number;
  username: string;
  email?: string;
}

interface GroupDetail {
  id: number;
  name: string;
}

interface EventDetail {
  id: number;
  title: string;
  description?: string;
  checkin_mode?: "personal_qr" | "event_qr";
  date?: string;
  code?: string;
  auto_approve?: boolean;
  created_by?: { id: number; username: string };
  participant_details?: UserDetail[];
  group_details?: GroupDetail[];
}

const combineDateAndTime = (
  current: Date | null,
  mode: "date" | "time",
  incoming: Date
) => {
  if (mode === "date") {
    const next = new Date(incoming);
    if (current) {
      next.setHours(current.getHours(), current.getMinutes(), 0, 0);
    }
    return next;
  }

  const base = current ? new Date(current) : new Date();
  base.setHours(incoming.getHours(), incoming.getMinutes(), 0, 0);
  base.setSeconds(0, 0);
  return base;
};

export default function EventDetailScreen() {
  const { id } = useLocalSearchParams();
  const eventId = Array.isArray(id) ? id[0] : id;
  const { apiFetch, currentUser } = useAuth();
  const router = useRouter();
  const { timeFormat, dateFormat } = useSettings();
  const locale = timeFormat === "24" ? "en-GB" : "en-US";

  const [event, setEvent] = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showManageModal, setShowManageModal] = useState(false);
  const [managing, setManaging] = useState(false);
  const [availableUsers, setAvailableUsers] = useState<UserDetail[]>([]);
  const [availableGroups, setAvailableGroups] = useState<GroupDetail[]>([]);
  const [selectedUserIds, setSelectedUserIds] = useState<number[]>([]);
  const [selectedGroupIds, setSelectedGroupIds] = useState<number[]>([]);
  const [autoApprove, setAutoApprove] = useState(false);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);
  const [editDescription, setEditDescription] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editDateTime, setEditDateTime] = useState<Date | null>(null);
  const [pickerMode, setPickerMode] = useState<"date" | "time" | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [iosPickerValue, setIosPickerValue] = useState(() => new Date());
  const [scanModalVisible, setScanModalVisible] = useState(false);
  const [cameraFacing, setCameraFacing] = useState<CameraType>("back");
  const [permission, requestPermission] = useCameraPermissions();
  const [scanLoading, setScanLoading] = useState(false);
  const [pendingUser, setPendingUser] = useState<UserDetail | null>(null);
  const [pendingCode, setPendingCode] = useState<string | null>(null);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [scanStatus, setScanStatus] = useState<
    "idle" | "processing" | "pending" | "success" | "already" | "error"
  >("idle");
  const [lastScannedCode, setLastScannedCode] = useState<string | null>(null);
  const titleInputRef = useRef<TextInput | null>(null);
  const descriptionInputRef = useRef<TextInput | null>(null);

  const formatDateDisplay = useCallback(
    (value?: string) => {
      if (!value) return null;
      const parsed = new Date(value);
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
    },
    [dateFormat, timeFormat]
  );

  const loadEvent = useCallback(
    async ({ silent = false }: { silent?: boolean } = {}) => {
      if (!eventId) {
        if (!silent) {
          setError("Missing event id");
          setLoading(false);
        }
        return null;
      }

      if (!silent) {
        setLoading(true);
        setError(null);
      }

      try {
        const response = await apiFetch(`/api/events/${eventId}/`);
        if (!response.ok) {
          throw new Error(`Unable to load event (${response.status})`);
        }
        const data: EventDetail = await response.json();
        setEvent(data);
        return data;
      } catch (err) {
        console.error("Error fetching event", err);
        if (!silent) {
          setError("Could not load this event.");
        }
        return null;
      } finally {
        if (!silent) {
          setLoading(false);
        }
      }
    },
    [apiFetch, eventId]
  );

  useEffect(() => {
    loadEvent();
  }, [loadEvent]);

  const formattedDate = useMemo(
    () => formatDateDisplay(event?.date),
    [event?.date, formatDateDisplay]
  );

  const formatDateOnly = useCallback(
    (value?: Date | null) => {
      if (!value) return null;
      const month = `${value.getMonth() + 1}`.padStart(2, "0");
      const day = `${value.getDate()}`.padStart(2, "0");
      const year = `${value.getFullYear()}`;
      return dateFormat === "MDY" ? `${month}/${day}/${year}` : `${day}/${month}/${year}`;
    },
    [dateFormat]
  );

  const formatTimeOnly = useCallback(
    (value?: Date | null) => {
      if (!value) return null;
      return value.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: timeFormat === "12",
      });
    },
    [timeFormat]
  );

  const prepareSelections = useCallback(() => {
    if (!event) return;
    setSelectedUserIds(event.participant_details?.map((user) => user.id) ?? []);
    setSelectedGroupIds(event.group_details?.map((group) => group.id) ?? []);
    setEditTitle(event.title);
    setEditDescription(event.description ?? "");
    setAutoApprove(Boolean(event.auto_approve));
    if (event.date) {
      const parsed = new Date(event.date);
      setEditDateTime(Number.isNaN(parsed.getTime()) ? null : parsed);
    } else {
      setEditDateTime(null);
    }
  }, [event]);

  const loadAssociations = useCallback(async () => {
    setSupportLoading(true);
    setSupportError(null);
    try {
      const [usersRes, groupsRes] = await Promise.all([
        apiFetch("/api/users/"),
        apiFetch("/api/groups/"),
      ]);

      if (!usersRes.ok) {
        throw new Error(`Unable to load users (${usersRes.status})`);
      }
      if (!groupsRes.ok) {
        throw new Error(`Unable to load groups (${groupsRes.status})`);
      }

      const [usersData, groupsData]: [UserDetail[], GroupDetail[]] = await Promise.all([
        usersRes.json(),
        groupsRes.json(),
      ]);

      setAvailableUsers(usersData);
      setAvailableGroups(groupsData);
    } catch (err) {
      console.error("Error loading associations", err);
      setSupportError("Could not load users or groups. Try again.");
    } finally {
      setSupportLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    if (showManageModal) {
      prepareSelections();
      loadAssociations();
    }
  }, [showManageModal, prepareSelections, loadAssociations]);

  useEffect(() => {
    if (!scanModalVisible) return;
    if (!permission) {
      requestPermission();
      return;
    }
    if (!permission.granted && permission.canAskAgain) {
      requestPermission();
    }
  }, [scanModalVisible, permission, requestPermission]);

  const toggleUser = useCallback((id: number) => {
    setSelectedUserIds((prev) =>
      prev.includes(id) ? prev.filter((userId) => userId !== id) : [...prev, id]
    );
  }, []);

  const toggleGroup = useCallback((id: number) => {
    setSelectedGroupIds((prev) =>
      prev.includes(id) ? prev.filter((groupId) => groupId !== id) : [...prev, id]
    );
  }, []);

  const resetScanState = useCallback(() => {
    setPendingUser(null);
    setPendingCode(null);
    setScanMessage(null);
    setScanStatus("idle");
    setLastScannedCode(null);
    setScanLoading(false);
  }, []);

  const openScanModal = useCallback(() => {
    resetScanState();
    setScanModalVisible(true);
  }, [resetScanState]);

  const closeScanModal = useCallback(() => {
    setScanModalVisible(false);
    resetScanState();
  }, [resetScanState]);

  const toggleCameraFacing = useCallback(() => {
    setCameraFacing((current) => (current === "back" ? "front" : "back"));
  }, []);

  const closeManageModal = useCallback(() => {
    setShowManageModal(false);
    setSupportError(null);
    setManaging(false);
    setPickerMode(null);
    setShowPicker(false);
  }, []);

  const applyEditSelection = useCallback((mode: "date" | "time", value: Date) => {
    setEditDateTime((prev) => {
      const next = combineDateAndTime(prev, mode, value);
      setIosPickerValue(next ?? new Date());
      return next;
    });
  }, []);

  const updateIosEditValue = useCallback(
    (mode: "date" | "time", value: Date) => {
      setIosPickerValue((prev) => combineDateAndTime(prev, mode, value));
    },
    []
  );

  const openEditPicker = useCallback(
    (mode: "date" | "time") => {
      titleInputRef.current?.blur();
      descriptionInputRef.current?.blur();
      Keyboard.dismiss();
      if (Platform.OS === "android") {
        setTimeout(() => {
          DateTimePickerAndroid.open({
            value: editDateTime ?? new Date(),
            mode,
            is24Hour: timeFormat === "24",
            onChange: (_event, date) => {
              if (!date) return;
              applyEditSelection(mode, date);
            },
          });
        }, 80);
        return;
      }
      const base = editDateTime ?? new Date();
      setTimeout(() => {
        setIosPickerValue(base);
        setPickerMode(mode);
        setShowPicker(true);
      }, 80);
    },
    [editDateTime, applyEditSelection, timeFormat]
  );

  const handleSaveAssociations = useCallback(async () => {
    if (!eventId) return;
    setManaging(true);
    try {
      const response = await apiFetch(`/api/events/${eventId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: editTitle.trim(),
          participants: selectedUserIds,
          groups: selectedGroupIds,
          description: editDescription.trim(),
          date: editDateTime ? editDateTime.toISOString() : null,
          auto_approve: autoApprove,
        }),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        const message = errorBody?.detail ?? "Unable to update event.";
        throw new Error(message);
      }

      const updated: EventDetail = await response.json();
      setEvent(updated);
      setAutoApprove(Boolean(updated.auto_approve));
      const refreshed = await loadEvent({ silent: true });
      if (refreshed) {
        setAutoApprove(Boolean(refreshed.auto_approve));
      }
      closeManageModal();
      Alert.alert("Success", "Event updated successfully.");
    } catch (err: any) {
      console.error("Error updating event", err);
      Alert.alert("Error", err?.message ?? "Failed to update event.");
      setManaging(false);
    }
  }, [
    apiFetch,
    eventId,
    loadEvent,
    selectedUserIds,
    selectedGroupIds,
    closeManageModal,
    editTitle,
    editDescription,
    editDateTime,
  ]);

  const handleDelete = useCallback(() => {
    if (!eventId) return;
    Alert.alert("Delete Event", "Are you sure you want to delete this event?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            const response = await apiFetch(`/api/events/${eventId}/`, {
              method: "DELETE",
            });
            if (!response.ok) {
              const body = await response.json().catch(() => ({}));
              throw new Error(body?.detail ?? "Failed to delete event.");
            }
            Alert.alert("Event deleted");
            router.back();
          } catch (error: any) {
            console.error(error);
            Alert.alert("Error", error?.message ?? "Unable to delete event.");
          }
        },
      },
    ]);
  }, [eventId, apiFetch, router]);

  const handleBarCodeScanned = useCallback(
    async ({ data }: { data: string }) => {
      if (!scanModalVisible || !eventId) return;
      if (scanLoading || pendingUser || scanStatus === "processing" || scanStatus === "success" || scanStatus === "already") {
        return;
      }

      const trimmed = data?.trim();
      if (!trimmed) return;
      if (lastScannedCode && lastScannedCode === trimmed && scanStatus !== "idle") {
        return;
      }

      setScanLoading(true);
      setScanStatus("processing");
      setScanMessage(null);
      setLastScannedCode(trimmed);
      setPendingCode(trimmed);

      try {
        const response = await apiFetch(`/api/events/${eventId}/admit/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_code: trimmed,
            confirm: Boolean(event?.auto_approve),
          }),
        });

        const payload = await response.json().catch(() => ({}));

        if (response.status === 202 && payload?.status === "pending_confirmation") {
          setPendingUser(payload?.user ?? null);
          setScanStatus("pending");
          setScanMessage(payload?.message ?? "Manual approval required.");
        } else if (response.ok && payload?.status === "admitted") {
          setPendingUser(null);
          setPendingCode(null);
          setScanStatus("success");
          setScanMessage(payload?.message ?? "Attendee admitted successfully.");
          if (payload?.event) {
            setEvent(payload.event);
          } else {
            await loadEvent({ silent: true });
          }
        } else if (response.ok && payload?.status === "already_admitted") {
          setPendingUser(null);
          setPendingCode(null);
          setScanStatus("already");
          setScanMessage(payload?.message ?? "This attendee has already been admitted.");
          if (payload?.event) {
            setEvent(payload.event);
          }
        } else {
          const detail =
            (payload && (payload.detail ?? payload.message)) || "Could not process this QR code.";
          setScanStatus("error");
          setScanMessage(detail);
        }
      } catch (err) {
        console.error("Error admitting attendee", err);
        setScanStatus("error");
        setScanMessage("Something went wrong while processing the QR code.");
      } finally {
        setScanLoading(false);
      }
    },
    [
      apiFetch,
      event?.auto_approve,
      eventId,
      lastScannedCode,
      loadEvent,
      pendingUser,
      scanLoading,
      scanModalVisible,
      scanStatus,
    ]
  );

  const handleApprovePending = useCallback(async () => {
    if (!pendingCode || !eventId) return;
    setScanLoading(true);
    setScanStatus("processing");
    try {
      const response = await apiFetch(`/api/events/${eventId}/admit/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_code: pendingCode, confirm: true }),
      });

      const payload = await response.json().catch(() => ({}));

      if (response.ok && payload?.status === "admitted") {
        setPendingUser(null);
        setPendingCode(null);
        setScanStatus("success");
        setScanMessage(payload?.message ?? "Attendee admitted successfully.");
        if (payload?.event) {
          setEvent(payload.event);
        } else {
          await loadEvent({ silent: true });
        }
      } else if (response.ok && payload?.status === "already_admitted") {
        setPendingUser(null);
        setPendingCode(null);
        setScanStatus("already");
        setScanMessage(payload?.message ?? "This attendee has already been admitted.");
        if (payload?.event) {
          setEvent(payload.event);
        }
      } else {
        const detail =
          (payload && (payload.detail ?? payload.message)) || "Unable to approve attendee.";
        setScanStatus("error");
        setScanMessage(detail);
      }
    } catch (err) {
      console.error("Error approving attendee", err);
      setScanStatus("error");
      setScanMessage("Failed to approve attendee.");
    } finally {
      setScanLoading(false);
    }
  }, [apiFetch, eventId, loadEvent, pendingCode]);

  const handleRejectPending = useCallback(() => {
    resetScanState();
  }, [resetScanState]);

  if (loading) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#2563eb" />
          <Text style={styles.loadingText}>Loading event…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (error || !event) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <Text style={styles.errorText}>{error ?? "Event not found."}</Text>
        </View>
      </SafeAreaView>
    );
  }

  const isOwner = event.created_by?.id === currentUser?.id;

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.headerRow}>
          <Text style={styles.title}>{event.title}</Text>
          {isOwner && (
            <TouchableOpacity style={styles.deleteButton} onPress={handleDelete}>
              <Text style={styles.deleteButtonText}>Delete</Text>
            </TouchableOpacity>
          )}
        </View>
        {formattedDate && <Text style={styles.meta}>When: {formattedDate}</Text>}
        {event.description ? (
          <Text style={styles.description}>{event.description}</Text>
        ) : (
          <Text style={styles.placeholder}>No description provided for this event.</Text>
        )}

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Admission Mode</Text>
          <Text style={styles.modeText}>
            {event.auto_approve
              ? "Automatic — attendees are admitted as soon as their QR code is scanned."
              : "Manual — review each scanned QR code before admitting the attendee."}
          </Text>
        </View>

        <CheckinSection
          eventId={event.id}
          checkinMode={event.checkin_mode ?? "event_qr"}
          isOwner={isOwner}
          onResolved={() => loadEvent({ silent: true })}
        />

        <OppgaverSection eventId={event.id} isOwner={isOwner} />

        {isOwner && <PoolSection eventId={event.id} />}

        <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Participants</Text>
          {isOwner && (
            <View style={styles.headerActions}>
              <TouchableOpacity style={styles.scanActionButton} onPress={openScanModal}>
                <Text style={styles.scanActionButtonText}>Scan QR</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.manageButton}
                onPress={() => setShowManageModal(true)}
              >
                <Text style={styles.manageButtonText}>Manage</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
          {event.participant_details?.length ? (
            event.participant_details.map((user) => (
              <View style={styles.listRow} key={user.id}>
                <Text style={styles.listPrimary}>{user.username}</Text>
                {user.email ? <Text style={styles.listSecondary}>{user.email}</Text> : null}
              </View>
            ))
          ) : (
            <Text style={styles.placeholder}>No users attached to this event.</Text>
          )}
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Groups</Text>
          {event.group_details?.length ? (
            event.group_details.map((group) => (
              <View style={styles.listRow} key={group.id}>
                <Text style={styles.listPrimary}>{group.name}</Text>
              </View>
            ))
          ) : (
            <Text style={styles.placeholder}>No groups attached to this event.</Text>
          )}
        </View>

        {event.code && <Text style={styles.codeHint}>ID: {event.code}</Text>}
      </ScrollView>

      <Modal
        transparent
        animationType="slide"
        visible={showManageModal}
        onRequestClose={closeManageModal}
      >
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView
            behavior={Platform.OS === "ios" ? "padding" : undefined}
            style={styles.modalContainer}
          >
            <View style={styles.modalContent}>
              <Text style={styles.modalTitle}>Update event details</Text>
              <ScrollView contentContainerStyle={styles.modalScroll}>
                <TextInput
                  style={styles.input}
                  placeholder="Title"
                  value={editTitle}
                  ref={titleInputRef}
                  onChangeText={setEditTitle}
                />
                <Text style={styles.modalSectionHeading}>Date & Time</Text>
                <View style={styles.selectorGroup}>
                  <TouchableOpacity
                    style={styles.selectorButton}
                    onPress={() => openEditPicker("date")}
                  >
                    <Text style={styles.selectorText}>
                      {formatDateOnly(editDateTime) ?? "Select date"}
                    </Text>
                  </TouchableOpacity>

                  <TouchableOpacity
                    style={[styles.selectorButton, !editDateTime && styles.selectorDisabled]}
                    onPress={() => openEditPicker("time")}
                    disabled={!editDateTime && Platform.OS !== "ios"}
                  >
                    <Text
                      style={[
                        styles.selectorText,
                        !editDateTime && Platform.OS !== "ios" && styles.selectorTextDisabled,
                      ]}
                    >
                      {formatTimeOnly(editDateTime) ?? "Select time (optional)"}
                    </Text>
                  </TouchableOpacity>

                  {editDateTime && (
                    <TouchableOpacity
                      style={styles.clearButton}
                      onPress={() => {
                        setEditDateTime(null);
                        setIosPickerValue(new Date());
                      }}
                    >
                      <Text style={styles.clearButtonText}>Clear date</Text>
                    </TouchableOpacity>
                  )}

                  {Platform.OS === "ios" && showPicker && pickerMode && (
                    <View style={styles.iosPickerWrapper}>
                      <DateTimePicker
                        locale={locale}
                        value={iosPickerValue}
                        mode={pickerMode}
                        display="spinner"
                        themeVariant="dark"
                        textColor="#ffffff"
                        onChange={(event: DateTimePickerEvent, date?: Date) => {
                          if (event.type === "dismissed" || !date) return;
                          updateIosEditValue(pickerMode, date);
                        }}
                        style={styles.iosPicker}
                      />
                      <View style={styles.iosPickerActions}>
                        <TouchableOpacity
                          onPress={() => {
                            if (pickerMode) {
                              applyEditSelection(pickerMode, iosPickerValue);
                            }
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

                <Text style={styles.modalSectionHeading}>Admission Mode</Text>
                <View style={styles.switchRow}>
                  <View style={styles.switchTextBlock}>
                    <Text style={styles.switchTitle}>Auto-admit attendees</Text>
                    <Text style={styles.switchSubtitle}>
                      When enabled, scanned QR codes are approved instantly. Otherwise you can
                      review and approve each attendee manually.
                    </Text>
                  </View>
                  <Switch
                    value={autoApprove}
                    onValueChange={(next) => {
                      setAutoApprove(next);
                      setEvent((prev) => (prev ? { ...prev, auto_approve: next } : prev));
                    }}
                    thumbColor={autoApprove ? "#2563eb" : "#f8fafc"}
                    trackColor={{ true: "#bfdbfe", false: "#cbd5f5" }}
                  />
                </View>

                <Text style={styles.modalSectionHeading}>Description</Text>
                <TextInput
                  style={[styles.input, styles.multilineInput]}
                  multiline
                  placeholder="Describe the event"
                  value={editDescription}
                  ref={descriptionInputRef}
                  onChangeText={setEditDescription}
                />

                <Text style={styles.modalSectionHeading}>Users</Text>
                {supportLoading ? (
                  <ActivityIndicator color="#2563eb" />
                ) : supportError ? (
                  <>
                    <Text style={styles.helperText}>{supportError}</Text>
                    <TouchableOpacity style={styles.retryButton} onPress={loadAssociations}>
                      <Text style={styles.retryText}>Retry</Text>
                    </TouchableOpacity>
                  </>
                ) : availableUsers.length ? (
                  <View style={styles.chipWrap}>
                    {availableUsers.map((user) => {
                      const selected = selectedUserIds.includes(user.id);
                      return (
                        <TouchableOpacity
                          key={user.id}
                          style={[styles.chip, selected && styles.chipSelected]}
                          onPress={() => toggleUser(user.id)}
                        >
                          <Text style={[styles.chipText, selected && styles.chipTextSelected]}>
                            {user.username}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                ) : (
                  <Text style={styles.helperText}>No users available.</Text>
                )}

                <Text style={styles.modalSectionHeading}>Groups</Text>
                {supportLoading ? (
                  <ActivityIndicator color="#2563eb" />
                ) : availableGroups.length ? (
                  <View style={styles.chipWrap}>
                    {availableGroups.map((group) => {
                      const selected = selectedGroupIds.includes(group.id);
                      return (
                        <TouchableOpacity
                          key={group.id}
                          style={[styles.chip, selected && styles.chipSelected]}
                          onPress={() => toggleGroup(group.id)}
                        >
                          <Text style={[styles.chipText, selected && styles.chipTextSelected]}>
                            {group.name}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                ) : (
                  <Text style={styles.helperText}>No groups available.</Text>
                )}
              </ScrollView>

              <View style={styles.modalButtons}>
                <TouchableOpacity
                  style={[styles.modalButton, styles.cancelButton]}
                  onPress={closeManageModal}
                  disabled={managing}
                >
                  <Text style={styles.cancelButtonText}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.modalButton, styles.saveButton]}
                  onPress={handleSaveAssociations}
                  disabled={managing}
                >
                  <Text style={styles.saveButtonText}>
                    {managing ? "Saving..." : "Save"}
                  </Text>
                </TouchableOpacity>
              </View>
        </View>
      </KeyboardAvoidingView>
    </View>
  </Modal>

      <Modal
        animationType="slide"
        visible={scanModalVisible}
        onRequestClose={closeScanModal}
      >
        <SafeAreaView style={styles.scanSafeArea}>
          <View style={styles.scanWrapper}>
            <View style={styles.scanHeader}>
              <TouchableOpacity onPress={closeScanModal} style={styles.scanHeaderButton}>
                <Text style={styles.scanHeaderButtonText}>Close</Text>
              </TouchableOpacity>
              <Text style={styles.scanHeaderTitle}>Scan Attendees</Text>
              <TouchableOpacity
                onPress={toggleCameraFacing}
                style={[styles.scanHeaderButton, !permission?.granted && styles.scanHeaderButtonDisabled]}
                disabled={!permission?.granted}
              >
                <Text
                  style={[
                    styles.scanHeaderButtonText,
                    !permission?.granted && styles.scanHeaderButtonDisabledText,
                  ]}
                >
                  Flip
                </Text>
              </TouchableOpacity>
            </View>

            <View style={styles.scanModeBanner}>
              <Text style={styles.scanModeBannerText}>
                {event.auto_approve
                  ? "Automatic admission enabled"
                  : "Manual approval required"}
              </Text>
            </View>

            <View style={styles.cameraContainer}>
              {!permission ? (
                <View style={styles.cameraPlaceholder}>
                  <ActivityIndicator color="#f8fafc" />
                  <Text style={styles.cameraPlaceholderText}>Checking camera permission…</Text>
                </View>
              ) : !permission.granted ? (
                <View style={styles.cameraPlaceholder}>
                  <Text style={styles.cameraPlaceholderText}>
                    We need camera access to scan QR codes.
                  </Text>
                  {permission.canAskAgain && (
                    <TouchableOpacity
                      style={[styles.scanButton, styles.scanButtonPrimary, styles.scanButtonFull]}
                      onPress={requestPermission}
                      disabled={scanLoading}
                    >
                      <Text style={styles.scanButtonText}>Grant Access</Text>
                    </TouchableOpacity>
                  )}
                </View>
              ) : (
                <View style={styles.cameraFrame}>
                  <CameraView
                    style={styles.camera}
                    facing={cameraFacing}
                    onBarcodeScanned={handleBarCodeScanned}
                    barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
                  />
                  {scanLoading && (
                    <View style={styles.cameraOverlay}>
                      <ActivityIndicator color="#f8fafc" />
                    </View>
                  )}
                </View>
              )}
            </View>

            <View style={styles.scanFooter}>
              {!pendingUser && !scanMessage && scanStatus === "idle" && (
                <Text style={styles.scanHint}>Align a QR code within the frame to begin.</Text>
              )}

              {scanMessage && (
                <Text
                  style={[
                    styles.scanFeedback,
                    scanStatus === "success" && styles.scanFeedbackSuccess,
                    scanStatus === "already" && styles.scanFeedbackInfo,
                    scanStatus === "error" && styles.scanFeedbackError,
                  ]}
                >
                  {scanMessage}
                </Text>
              )}

              {pendingUser && (
                <View style={styles.pendingCard}>
                  <Text style={styles.pendingTitle}>Review attendee</Text>
                  <Text style={styles.pendingName}>{pendingUser.username}</Text>
                  {pendingUser.email ? (
                    <Text style={styles.pendingEmail}>{pendingUser.email}</Text>
                  ) : null}
                  <View style={styles.pendingActions}>
                    <TouchableOpacity
                      style={[styles.scanButton, styles.scanButtonSecondary, styles.scanButtonGrow]}
                      onPress={handleRejectPending}
                      disabled={scanLoading}
                    >
                      <Text style={styles.scanButtonText}>Dismiss</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[styles.scanButton, styles.scanButtonPrimary, styles.scanButtonGrow]}
                      onPress={handleApprovePending}
                      disabled={scanLoading}
                    >
                      <Text style={styles.scanButtonText}>
                        {scanLoading ? "Working…" : "Approve"}
                      </Text>
                    </TouchableOpacity>
                  </View>
                </View>
              )}

              {!pendingUser &&
                (scanStatus === "success" || scanStatus === "already" || scanStatus === "error") && (
                  <TouchableOpacity
                    style={[styles.scanButton, styles.scanButtonSecondary, styles.scanButtonFull]}
                    onPress={resetScanState}
                    disabled={scanLoading}
                  >
                    <Text style={styles.scanButtonText}>Scan Next</Text>
                  </TouchableOpacity>
                )}

              <TouchableOpacity
                style={[styles.scanButton, styles.scanButtonGhost, styles.scanButtonFull]}
                onPress={closeScanModal}
                disabled={scanLoading}
              >
                <Text style={styles.scanButtonGhostText}>Close Scanner</Text>
              </TouchableOpacity>
            </View>
          </View>
        </SafeAreaView>
      </Modal>

    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: "#f8fafc" },
  container: { padding: 24, gap: 24 },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  title: { fontSize: 26, fontWeight: "700", color: "#0f172a", flexShrink: 1 },
  meta: { fontSize: 16, color: "#475569" },
  description: { fontSize: 16, lineHeight: 22, color: "#1f2937" },
  placeholder: { fontSize: 16, color: "#94a3b8" },
  deleteButton: {
    backgroundColor: "#ef4444",
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 999,
  },
  deleteButtonText: { color: "white", fontWeight: "600" },
  section: { gap: 12 },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  sectionTitle: { fontSize: 20, fontWeight: "600", color: "#0f172a" },
  modeText: { fontSize: 14, color: "#475569" },
  listRow: {
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#d6e0f0",
  },
  listPrimary: { fontSize: 16, fontWeight: "500", color: "#1f2937" },
  listSecondary: { fontSize: 14, color: "#64748b", marginTop: 2 },
  headerActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  manageButton: {
    backgroundColor: "#2563eb",
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 999,
  },
  manageButtonText: { color: "white", fontWeight: "600" },
  scanActionButton: {
    backgroundColor: "#0f172a",
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#94a3b8",
  },
  scanActionButtonText: { color: "#e2e8f0", fontWeight: "600" },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20 },
  loadingText: { marginTop: 12, fontSize: 16, color: "#475569" },
  errorText: { fontSize: 16, color: "#b91c1c", textAlign: "center" },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(15,23,42,0.45)",
    justifyContent: "center",
    padding: 20,
  },
  modalContainer: {
    flex: 1,
    justifyContent: "center",
  },
  modalContent: {
    backgroundColor: "#fff",
    borderRadius: 18,
    padding: 20,
    gap: 20,
    maxHeight: "90%",
  },
  modalTitle: { fontSize: 20, fontWeight: "700", textAlign: "center", color: "#0f172a" },
  modalScroll: { gap: 16 },
  selectorGroup: { gap: 10 },
  selectorButton: {
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 14,
    backgroundColor: "#f8fafc",
  },
  selectorText: { color: "#0f172a", fontWeight: "500" },
  selectorDisabled: { opacity: 0.5 },
  selectorTextDisabled: { color: "#94a3b8" },
  input: {
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    backgroundColor: "#f8fafc",
    color: "#0f172a",
  },
  multilineInput: { minHeight: 80, textAlignVertical: "top" },
  clearButton: { alignSelf: "flex-start" },
  clearButtonText: { color: "#ef4444", fontWeight: "600" },
  modalSectionHeading: { fontSize: 16, fontWeight: "600", color: "#0f172a" },
  helperText: { fontSize: 14, color: "#64748b" },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },
  switchTextBlock: { flex: 1, paddingRight: 12 },
  switchTitle: { fontSize: 16, fontWeight: "600", color: "#0f172a" },
  switchSubtitle: { fontSize: 13, color: "#64748b", marginTop: 4 },
  chipWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    columnGap: 8,
    rowGap: 8,
  },
  chip: {
    borderRadius: 20,
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: "#d0d7e2",
    backgroundColor: "#fff",
  },
  chipSelected: {
    backgroundColor: "#1d4ed8",
    borderColor: "#1d4ed8",
  },
  chipText: { color: "#1f2937", fontSize: 14, fontWeight: "500" },
  chipTextSelected: { color: "#f8fafc" },
  retryButton: { alignSelf: "flex-start" },
  retryText: { color: "#2563eb", fontWeight: "600" },
  modalButtons: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 12,
  },
  modalButton: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 8,
  },
  cancelButton: { backgroundColor: "#e2e8f0" },
  cancelButtonText: { color: "#334155", fontWeight: "500" },
  saveButton: { backgroundColor: "#2563eb" },
  saveButtonText: { color: "white", fontWeight: "600" },
  codeHint: {
    marginTop: 16,
    fontSize: 12,
    color: "#94a3b8",
    textTransform: "uppercase",
  },
  iosPickerWrapper: {
    width: "100%",
    backgroundColor: "#0f172a",
    borderRadius: 16,
    marginTop: 12,
    paddingVertical: 12,
    paddingHorizontal: 10,
    gap: 8,
    borderWidth: 1,
    borderColor: "rgba(37,99,235,0.35)",
  },
  iosPicker: { alignSelf: "stretch", backgroundColor: "transparent" },
  iosPickerActions: { alignItems: "flex-end" },
  iosPickerActionText: { color: "#bfdbfe", fontWeight: "600" },
  scanSafeArea: { flex: 1, backgroundColor: "#020617" },
  scanWrapper: { flex: 1, padding: 16, gap: 16 },
  scanHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  scanHeaderButton: { paddingVertical: 6, paddingHorizontal: 10 },
  scanHeaderButtonText: { color: "#bfdbfe", fontWeight: "600" },
  scanHeaderButtonDisabled: { opacity: 0.4 },
  scanHeaderButtonDisabledText: { color: "#64748b" },
  scanHeaderTitle: { fontSize: 18, fontWeight: "700", color: "#f8fafc" },
  scanModeBanner: {
    alignSelf: "flex-start",
    backgroundColor: "#1e293b",
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderRadius: 999,
  },
  scanModeBannerText: {
    color: "#cbd5f5",
    fontSize: 12,
    fontWeight: "600",
    letterSpacing: 0.6,
    textTransform: "uppercase",
  },
  cameraContainer: { flex: 1, justifyContent: "center" },
  cameraFrame: {
    flex: 1,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: "#1e293b",
    overflow: "hidden",
    backgroundColor: "#000",
  },
  camera: { flex: 1 },
  cameraOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(15,23,42,0.45)",
    alignItems: "center",
    justifyContent: "center",
  },
  cameraPlaceholder: {
    flex: 1,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: "#1e293b",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    backgroundColor: "#0f172a",
    gap: 16,
  },
  cameraPlaceholderText: { color: "#e2e8f0", textAlign: "center", fontSize: 14 },
  scanFooter: { gap: 16 },
  scanHint: { color: "#94a3b8", textAlign: "center" },
  scanFeedback: { color: "#e2e8f0", textAlign: "center", fontWeight: "600" },
  scanFeedbackSuccess: { color: "#4ade80" },
  scanFeedbackInfo: { color: "#22d3ee" },
  scanFeedbackError: { color: "#f87171" },
  pendingCard: {
    backgroundColor: "#0f172a",
    borderRadius: 16,
    padding: 16,
    gap: 8,
    borderWidth: 1,
    borderColor: "#1e293b",
  },
  pendingTitle: { color: "#bfdbfe", fontWeight: "600", fontSize: 16 },
  pendingName: { color: "#f8fafc", fontSize: 18, fontWeight: "700" },
  pendingEmail: { color: "#cbd5f5", fontSize: 14 },
  pendingActions: { flexDirection: "row", gap: 12, marginTop: 12 },
  scanButton: {
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  scanButtonGrow: { flex: 1 },
  scanButtonFull: { alignSelf: "stretch" },
  scanButtonPrimary: { backgroundColor: "#2563eb", borderColor: "#2563eb" },
  scanButtonSecondary: { backgroundColor: "transparent", borderColor: "#334155" },
  scanButtonText: { color: "#e2e8f0", fontWeight: "600" },
  scanButtonGhost: { backgroundColor: "transparent", borderColor: "transparent" },
  scanButtonGhostText: { color: "#94a3b8", fontWeight: "600" },
});
