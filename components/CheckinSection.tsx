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
import { useAuth } from "../app/AuthContext";
import type { Shift } from "./OppgaverSection";

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
  checkinMode,
  isOwner,
  onResolved,
}: {
  eventId: number;
  checkinMode: "personal_qr" | "event_qr";
  isOwner: boolean;
  onResolved?: () => void;
}) {
  const { apiFetch } = useAuth();
  const [selfChecking, setSelfChecking] = useState(false);
  const [selfResult, setSelfResult] = useState<CheckinResult | null>(null);

  const [scanModalVisible, setScanModalVisible] = useState(false);
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

  const handleSelfCheckin = useCallback(async () => {
    setSelfChecking(true);
    setSelfResult(null);
    try {
      const response = await apiFetch(`/api/events/${eventId}/self-checkin/`, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok && response.status !== 202) {
        throw new Error(body?.detail ?? "Unable to check in.");
      }
      setSelfResult(body as CheckinResult);
      onResolved?.();
    } catch (err: any) {
      console.error("Error self-checking in", err);
      Alert.alert("Error", err?.message ?? "Failed to check in.");
    } finally {
      setSelfChecking(false);
    }
  }, [apiFetch, eventId, onResolved]);

  const resetScanState = useCallback(() => {
    setScanResult(null);
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

  const handleBarCodeScanned = useCallback(
    async ({ data }: { data: string }) => {
      if (!scanModalVisible || scanLoading) return;
      const trimmed = data?.trim();
      if (!trimmed || trimmed === lastScannedCode) return;

      setScanLoading(true);
      setLastScannedCode(trimmed);
      try {
        const response = await apiFetch(`/api/events/${eventId}/checkin/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_code: trimmed }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok && response.status !== 202) {
          throw new Error(body?.detail ?? "Could not process this QR code.");
        }
        setScanResult(body as CheckinResult);
        onResolved?.();
      } catch (err: any) {
        console.error("Error checking in attendee", err);
        Alert.alert("Error", err?.message ?? "Failed to process QR code.");
      } finally {
        setScanLoading(false);
      }
    },
    [apiFetch, eventId, lastScannedCode, onResolved, scanLoading, scanModalVisible]
  );

  if (checkinMode === "event_qr") {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Check-in</Text>
        <Text style={styles.helperText}>
          Volunteers check themselves in — no admin scanning needed for this event.
        </Text>
        <TouchableOpacity
          style={[styles.primaryButton, selfChecking && styles.buttonDisabled]}
          onPress={handleSelfCheckin}
          disabled={selfChecking}
        >
          <Text style={styles.primaryButtonText}>{selfChecking ? "Checking in…" : "Check in"}</Text>
        </TouchableOpacity>
        {selfResult && <Text style={styles.resultText}>{describeResult(selfResult)}</Text>}
      </View>
    );
  }

  if (!isOwner) {
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
      <TouchableOpacity style={styles.primaryButton} onPress={openScanModal}>
        <Text style={styles.primaryButtonText}>Scan to check in</Text>
      </TouchableOpacity>

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
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 10, marginBottom: 20 },
  sectionTitle: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  helperText: { fontSize: 13, color: "#64748b" },
  primaryButton: { backgroundColor: "#2563eb", paddingVertical: 12, borderRadius: 10, alignItems: "center" },
  primaryButtonText: { color: "white", fontWeight: "700" },
  buttonDisabled: { opacity: 0.5 },
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
  scanAgainButtonText: { color: "#bfdbfe", fontWeight: "600" },
});
