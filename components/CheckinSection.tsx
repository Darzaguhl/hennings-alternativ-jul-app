import { useCallback, useEffect, useState } from "react";
import { CameraType, CameraView, useCameraPermissions } from "expo-camera";
import {
  ActivityIndicator,
  Alert,
  Modal,
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import QRCode from "react-native-qrcode-svg";
import { theme } from "../constants/theme";
import { useAuth } from "../app/AuthContext";
import type { Shift } from "./VakterSection";

type CheckinStatus = "assigned" | "already_assigned" | "pending_pool";

interface CheckinResult {
  status: CheckinStatus;
  message?: string;
  shift?: Shift;
  candidates?: Shift[];
}

const describeResult = (result: CheckinResult) => result.message ?? "Checked in.";

export default function CheckinSection({
  eventId,
  eventCode,
  checkinMode,
  isCheckinStaff,
  onResolved,
}: {
  eventId: number;
  eventCode?: string;
  checkinMode: "personal_qr" | "event_qr";
  isCheckinStaff: boolean;
  onResolved?: () => void;
}) {
  const { apiFetch } = useAuth();

  const [displayModalVisible, setDisplayModalVisible] = useState(false);
  const [scanModalVisible, setScanModalVisible] = useState(false);
  const [scanPurpose, setScanPurpose] = useState<"self" | "attendee">("attendee");
  const [permission, requestPermission] = useCameraPermissions();
  const [facing, setFacing] = useState<CameraType>("back");
  const [scanLoading, setScanLoading] = useState(false);
  const [scanResult, setScanResult] = useState<CheckinResult | null>(null);
  const [lastScannedCode, setLastScannedCode] = useState<string | null>(null);

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

  const resetScanState = useCallback(() => {
    setScanResult(null);
    setLastScannedCode(null);
    setScanLoading(false);
  }, []);

  const openScanModal = useCallback(
    (purpose: "self" | "attendee") => {
      resetScanState();
      setScanPurpose(purpose);
      setScanModalVisible(true);
    },
    [resetScanState]
  );

  const closeScanModal = useCallback(() => {
    setScanModalVisible(false);
    resetScanState();
  }, [resetScanState]);

  const performSelfCheckin = useCallback(async () => {
    const response = await apiFetch(`/api/events/${eventId}/self-checkin/`, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok && response.status !== 202) {
      throw new Error(body?.detail ?? "Unable to check in.");
    }
    return body as CheckinResult;
  }, [apiFetch, eventId]);

  const performAttendeeCheckin = useCallback(
    async (code: string) => {
      const response = await apiFetch(`/api/events/${eventId}/checkin/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_code: code }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok && response.status !== 202) {
        throw new Error(body?.detail ?? "Could not process this QR code.");
      }
      return body as CheckinResult;
    },
    [apiFetch, eventId]
  );

  const handleBarCodeScanned = useCallback(
    async ({ data }: { data: string }) => {
      if (!scanModalVisible || scanLoading) return;
      const trimmed = data?.trim();
      if (!trimmed || trimmed === lastScannedCode) return;

      if (scanPurpose === "self" && eventCode && trimmed !== eventCode) {
        setLastScannedCode(trimmed);
        Alert.alert("Wrong code", "That doesn't look like this event's check-in code. Ask an organizer where it's displayed.");
        return;
      }

      setScanLoading(true);
      setLastScannedCode(trimmed);
      try {
        const result = scanPurpose === "self" ? await performSelfCheckin() : await performAttendeeCheckin(trimmed);
        setScanResult(result);
        onResolved?.();
      } catch (err: any) {
        console.error("Error processing check-in scan", err);
        Alert.alert("Error", err?.message ?? "Failed to process QR code.");
      } finally {
        setScanLoading(false);
      }
    },
    [eventCode, lastScannedCode, onResolved, performAttendeeCheckin, performSelfCheckin, scanLoading, scanModalVisible, scanPurpose]
  );

  const scanModal = (
    <Modal visible={scanModalVisible} animationType="slide" onRequestClose={closeScanModal}>
      <SafeAreaView style={styles.scanSafeArea}>
        <View style={styles.scanHeader}>
          <TouchableOpacity onPress={closeScanModal} style={styles.scanHeaderButton}>
            <Text style={styles.scanHeaderButtonText}>Close</Text>
          </TouchableOpacity>
          <Text style={styles.scanHeaderTitle}>Check In</Text>
          <TouchableOpacity
            onPress={() => setFacing((current) => (current === "back" ? "front" : "back"))}
            style={styles.scanHeaderButton}
          >
            <Text style={styles.scanHeaderButtonText}>Flip</Text>
          </TouchableOpacity>
        </View>

        {permission?.granted ? (
          <CameraView style={styles.camera} facing={facing} onBarcodeScanned={handleBarCodeScanned} />
        ) : (
          <View style={styles.centered}>
            <Text style={styles.helperText}>Camera permission is required to scan QR codes.</Text>
          </View>
        )}

        <View style={styles.scanResultArea}>
          {scanLoading && <ActivityIndicator />}
          {scanResult && (
            <>
              <Text style={styles.resultText}>{describeResult(scanResult)}</Text>
              {scanResult.status === "pending_pool" && scanResult.candidates?.length ? (
                <Text style={styles.helperText}>
                  Candidates: {scanResult.candidates.map((c) => c.title).join(", ")} — resolve in the Pool section.
                </Text>
              ) : null}
              <TouchableOpacity onPress={resetScanState} style={styles.scanAgainButton}>
                <Text style={styles.scanAgainButtonText}>Scan Again</Text>
              </TouchableOpacity>
            </>
          )}
        </View>
      </SafeAreaView>
    </Modal>
  );

  if (checkinMode === "event_qr") {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Check-in</Text>
        <Text style={styles.helperText}>
          Scan the event's check-in code (displayed by an organizer) to check yourself in — no admin scanning needed.
        </Text>
        <TouchableOpacity style={styles.primaryButton} onPress={() => openScanModal("self")}>
          <Text style={styles.primaryButtonText}>Scan to check in</Text>
        </TouchableOpacity>
        {isCheckinStaff && eventCode && (
          <TouchableOpacity style={styles.secondaryButton} onPress={() => setDisplayModalVisible(true)}>
            <Text style={styles.secondaryButtonText}>Show check-in code</Text>
          </TouchableOpacity>
        )}
        {scanModal}
        {isCheckinStaff && eventCode && (
          <Modal
            visible={displayModalVisible}
            animationType="slide"
            transparent
            onRequestClose={() => setDisplayModalVisible(false)}
          >
            <View style={styles.displayOverlay}>
              <View style={styles.displayCard}>
                <Text style={styles.displayTitle}>Show this to volunteers</Text>
                <View style={styles.displayQrWrapper}>
                  <QRCode value={eventCode} size={220} color={theme.primaryDark} />
                </View>
                <Text style={styles.helperText}>
                  Display this on a screen or printed poster at the entrance — volunteers scan it with the app to
                  check themselves in.
                </Text>
                <TouchableOpacity style={styles.primaryButton} onPress={() => setDisplayModalVisible(false)}>
                  <Text style={styles.primaryButtonText}>Done</Text>
                </TouchableOpacity>
              </View>
            </View>
          </Modal>
        )}
      </View>
    );
  }

  if (!isCheckinStaff) {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Check-in</Text>
        <Text style={styles.helperText}>This event uses personal-QR check-in — ask an admin to scan your badge.</Text>
      </View>
    );
  }

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>Check-in</Text>
      <Text style={styles.helperText}>Scan a volunteer&apos;s personal QR code to check them in.</Text>
      <TouchableOpacity style={styles.primaryButton} onPress={() => openScanModal("attendee")}>
        <Text style={styles.primaryButtonText}>Scan to check in</Text>
      </TouchableOpacity>
      {scanModal}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 10, marginBottom: 20 },
  sectionTitle: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  helperText: { fontSize: 13, color: "#64748b" },
  primaryButton: { backgroundColor: theme.accent, paddingVertical: 12, borderRadius: 10, alignItems: "center" },
  primaryButtonText: { color: theme.primaryDark, fontWeight: "700" },
  buttonDisabled: { opacity: 0.5 },
  secondaryButton: {
    borderWidth: 1,
    borderColor: theme.accent,
    paddingVertical: 10,
    borderRadius: 10,
    alignItems: "center",
  },
  secondaryButtonText: { color: theme.primaryDark, fontWeight: "600" },
  displayOverlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "center", padding: 24 },
  displayCard: { backgroundColor: "#fff", borderRadius: 16, padding: 24, alignItems: "center", gap: 14 },
  displayTitle: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  displayQrWrapper: { padding: 16, backgroundColor: "#fff", borderRadius: 12, borderWidth: 1, borderColor: "#e2e8f0" },
  resultText: { fontSize: 14, color: "#0f172a", fontWeight: "600" },
  scanSafeArea: { flex: 1, backgroundColor: "#000" },
  scanHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  scanHeaderButton: { padding: 8 },
  scanHeaderButtonText: { color: "#fff", fontWeight: "600" },
  scanHeaderTitle: { color: "#fff", fontWeight: "700", fontSize: 16 },
  camera: { flex: 1 },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20 },
  scanResultArea: { padding: 16, gap: 8, backgroundColor: "#0f172a" },
  scanAgainButton: { alignSelf: "flex-start" },
  scanAgainButtonText: { color: theme.accentLight, fontWeight: "600" },
});
