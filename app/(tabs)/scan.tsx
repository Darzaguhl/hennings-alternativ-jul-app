// app/scan.tsx
import { CameraType, CameraView, useCameraPermissions } from "expo-camera";
import React, { useState } from "react";
import { Alert, Button, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { useAuth } from "../AuthContext";

export default function ScanScreen() {
  const [facing, setFacing] = useState<CameraType>("back");
  const [scanned, setScanned] = useState(false);
  const [scanData, setScanData] = useState<string | null>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const { apiFetch } = useAuth();

  if (!permission) return <View />;

  if (!permission.granted) {
    return (
      <View style={styles.container}>
        <Text style={styles.message}>We need your permission to use the camera</Text>
        <Button onPress={requestPermission} title="Grant Permission" />
      </View>
    );
  }

  function toggleCameraFacing() {
    setFacing((current) => (current === "back" ? "front" : "back"));
  }

  async function handleBarCodeScanned({ data }: { data: string }) {
    setScanned(true);
    setScanData(data);

    try {
      const response = await apiFetch("http://192.168.1.215:8000/api/qrcodes/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code_data: data }),
      });

      if (response.ok) {
        Alert.alert("Success", "QR Code saved to your account!");
      } else {
        const err = await response.json();
        Alert.alert("Error", JSON.stringify(err));
      }
    } catch (err) {
      console.error(err);
      Alert.alert("Error", "Failed to save QR Code to backend.");
    }
  }

  return (
    <View style={styles.container}>
      {!scanned ? (
        <>
          <CameraView style={styles.camera} facing={facing} onBarCodeScanned={handleBarCodeScanned} />
          <View style={styles.buttonOverlay}>
            <TouchableOpacity style={styles.button} onPress={toggleCameraFacing}>
              <Text style={styles.text}>Flip Camera</Text>
            </TouchableOpacity>
          </View>
        </>
      ) : (
        <View style={styles.result}>
          <Text style={styles.title}>QR Code Scanned!</Text>
          <Text style={styles.data}>{scanData}</Text>
          <Button title="Scan Again" onPress={() => setScanned(false)} />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  camera: { flex: 1 },
  buttonOverlay: { position: "absolute", bottom: 40, left: 0, right: 0, flexDirection: "row", justifyContent: "center" },
  button: { backgroundColor: "#00000080", padding: 12, borderRadius: 8 },
  text: { fontSize: 18, color: "white" },
  message: { textAlign: "center", paddingBottom: 10 },
  result: { flex: 1, justifyContent: "center", alignItems: "center", padding: 20 },
  title: { fontSize: 22, fontWeight: "bold", marginBottom: 10 },
  data: { fontSize: 18, marginBottom: 20 },
});
