import { useCallback, useEffect, useRef, useState } from "react";
import { CameraType, CameraView, useCameraPermissions } from "expo-camera";
import {
  ActivityIndicator,
  Image,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { colors, fonts, theme } from "../../constants/theme";
import { useAuth } from "../AuthContext";

interface InventoryCategoryOption {
  id: number;
  name: string;
}

interface IdentifySuggestion {
  category_guess?: string;
  description_guess?: string;
  quantity_guess?: number;
}

// "camera": take a photo. "analyzing": AI suggestion call in flight (see
// api.vision.identify_item on the backend). "form": confirm/edit the
// suggestion before saving -- quantity especially is never trusted from
// the AI alone, see InventoryItem's docstring on the backend for why.
// "saving": the actual POST that persists the record + photo.
// "success": confirmation, with a clear way to log another right away.
type Step = "camera" | "analyzing" | "form" | "saving" | "success";

export default function LagerScreen() {
  const { apiFetch } = useAuth();
  const [permission, requestPermission] = useCameraPermissions();
  const [facing] = useState<CameraType>("back");
  const cameraRef = useRef<CameraView>(null);

  const [step, setStep] = useState<Step>("camera");
  const [photoUri, setPhotoUri] = useState<string | null>(null);

  const [eventId, setEventId] = useState<number | null>(null);
  const [categories, setCategories] = useState<InventoryCategoryOption[]>([]);

  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null);
  const [description, setDescription] = useState("");
  const [quantity, setQuantity] = useState("1");

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!permission) {
      requestPermission();
    } else if (!permission.granted && permission.canAskAgain) {
      requestPermission();
    }
  }, [permission, requestPermission]);

  const loadReferenceData = useCallback(async () => {
    try {
      const [eventsRes, categoriesRes] = await Promise.all([
        apiFetch("/api/events/"),
        apiFetch("/api/inventory-categories/"),
      ]);
      if (eventsRes.ok) {
        const events: { id: number; is_active?: boolean }[] = await eventsRes.json();
        const sorted = [...events].sort((a, b) => b.id - a.id);
        setEventId(sorted.find((e) => e.is_active)?.id ?? sorted[0]?.id ?? null);
      }
      if (categoriesRes.ok) {
        setCategories(await categoriesRes.json());
      }
    } catch (err) {
      console.error("Error loading Lager reference data", err);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadReferenceData();
  }, [loadReferenceData]);

  const resetToCamera = useCallback(() => {
    setStep("camera");
    setPhotoUri(null);
    setSelectedCategoryId(null);
    setDescription("");
    setQuantity("1");
    setError(null);
  }, []);

  const photoFormPart = (uri: string) => ({ uri, name: "item.jpg", type: "image/jpeg" }) as unknown as Blob;

  const handleCapture = useCallback(async () => {
    if (!cameraRef.current) return;
    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.5 });
      if (!photo?.uri) return;
      setPhotoUri(photo.uri);
      setStep("analyzing");

      // Best-effort only -- if this fails or ANTHROPIC_API_KEY isn't set
      // on the backend, identify_item returns {} and the volunteer just
      // fills the form manually. Never blocks getting to the form.
      let suggestion: IdentifySuggestion = {};
      try {
        const formData = new FormData();
        formData.append("photo", photoFormPart(photo.uri));
        const response = await apiFetch("/api/inventory/identify/", { method: "POST", body: formData });
        if (response.ok) suggestion = await response.json();
      } catch (err) {
        console.error("Error identifying inventory photo", err);
      }

      if (suggestion.category_guess) {
        const guess = suggestion.category_guess.toLowerCase();
        const match = categories.find(
          (c) => c.name.toLowerCase().includes(guess) || guess.includes(c.name.toLowerCase())
        );
        if (match) setSelectedCategoryId(match.id);
      }
      if (suggestion.description_guess) setDescription(suggestion.description_guess);
      if (suggestion.quantity_guess) setQuantity(String(suggestion.quantity_guess));

      setStep("form");
    } catch (err) {
      console.error("Error capturing photo", err);
      setStep("camera");
    }
  }, [apiFetch, categories]);

  const handleSubmit = useCallback(async () => {
    if (!eventId || !selectedCategoryId || !photoUri) return;
    setStep("saving");
    setError(null);
    try {
      const formData = new FormData();
      formData.append("event", String(eventId));
      formData.append("category", String(selectedCategoryId));
      formData.append("quantity", String(Number(quantity) || 1));
      if (description.trim()) formData.append("description", description.trim());
      // Uploaded again here (not reused from the identify call) -- this is
      // the request that actually persists the photo as part of the record.
      formData.append("photo", photoFormPart(photoUri));

      const response = await apiFetch("/api/inventory-items/", { method: "POST", body: formData });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? body?.category?.[0] ?? "Kunne ikke registrere gjenstanden.");
      }
      setStep("success");
    } catch (err: any) {
      console.error("Error saving inventory item", err);
      setError(err?.message ?? "Noe gikk galt.");
      setStep("form");
    }
  }, [apiFetch, eventId, selectedCategoryId, photoUri, quantity, description]);

  if (step === "camera") {
    return (
      <View style={styles.container}>
        {permission?.granted ? (
          <>
            <CameraView ref={cameraRef} style={styles.camera} facing={facing} />
            <View style={styles.captureBar}>
              <Text style={styles.captureHint}>Ta bilde av gjenstanden(e) du vil registrere</Text>
              <TouchableOpacity style={styles.shutterButton} onPress={handleCapture} />
            </View>
          </>
        ) : (
          <View style={styles.centered}>
            <Text style={styles.helperText}>Kameratilgang er nødvendig for å registrere gjenstander.</Text>
          </View>
        )}
      </View>
    );
  }

  if (step === "analyzing") {
    return (
      <View style={styles.centered}>
        {photoUri && <Image source={{ uri: photoUri }} style={styles.previewImage} />}
        <ActivityIndicator size="large" color={theme.primary} style={{ marginTop: 16 }} />
        <Text style={styles.helperText}>Analyserer bildet …</Text>
      </View>
    );
  }

  if (step === "success") {
    return (
      <View style={styles.centered}>
        <Text style={styles.title}>Registrert!</Text>
        <TouchableOpacity style={styles.primaryButton} onPress={resetToCamera}>
          <Text style={styles.primaryButtonText}>Registrer en til</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      <Text style={styles.title}>Registrer gjenstand</Text>
      {photoUri && <Image source={{ uri: photoUri }} style={styles.previewImage} />}

      {error && <Text style={styles.error}>{error}</Text>}

      <Text style={styles.label}>Kategori</Text>
      <View style={styles.optionRow}>
        {categories.map((c) => {
          const selected = selectedCategoryId === c.id;
          return (
            <TouchableOpacity
              key={c.id}
              style={[styles.optionButton, selected && styles.optionButtonActive]}
              onPress={() => setSelectedCategoryId(c.id)}
            >
              <Text style={[styles.optionText, selected && styles.optionTextActive]}>{c.name}</Text>
            </TouchableOpacity>
          );
        })}
        {categories.length === 0 && (
          <Text style={styles.helperText}>Ingen kategorier lagt til ennå — be en admin legge til i Lager.</Text>
        )}
      </View>

      <Text style={styles.label}>Antall</Text>
      <TextInput style={styles.input} value={quantity} onChangeText={setQuantity} keyboardType="number-pad" />

      <Text style={styles.label}>Beskrivelse</Text>
      <TextInput
        style={styles.textArea}
        value={description}
        onChangeText={setDescription}
        placeholder="F.eks. Blå vinterjakke str M"
        multiline
      />

      <TouchableOpacity
        style={[styles.primaryButton, (!selectedCategoryId || step === "saving") && styles.buttonDisabled]}
        onPress={handleSubmit}
        disabled={!selectedCategoryId || step === "saving"}
      >
        <Text style={styles.primaryButtonText}>{step === "saving" ? "Registrerer …" : "Registrer"}</Text>
      </TouchableOpacity>

      <TouchableOpacity style={styles.secondaryButton} onPress={resetToCamera}>
        <Text style={styles.secondaryButtonText}>Avbryt / ta nytt bilde</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.background },
  centered: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: 20,
    backgroundColor: theme.background,
  },
  camera: { flex: 1 },
  captureBar: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    alignItems: "center",
    paddingBottom: 40,
    paddingTop: 20,
    backgroundColor: "rgba(15, 42, 27, 0.6)",
  },
  captureHint: {
    color: colors.white,
    fontFamily: fonts.body,
    marginBottom: 12,
    textAlign: "center",
    paddingHorizontal: 20,
  },
  shutterButton: {
    width: 70,
    height: 70,
    borderRadius: 35,
    backgroundColor: colors.white,
    borderWidth: 4,
    borderColor: theme.accent,
  },
  title: {
    fontSize: 22,
    fontFamily: fonts.displayBold,
    color: theme.primaryDark,
    marginBottom: 16,
    textAlign: "center",
  },
  previewImage: { width: 160, height: 160, borderRadius: 12, alignSelf: "center", marginBottom: 16 },
  helperText: { fontSize: 14, fontFamily: fonts.body, color: theme.textMuted, textAlign: "center", marginTop: 8 },
  error: { fontSize: 14, fontFamily: fonts.body, color: theme.danger, marginBottom: 12, textAlign: "center" },
  label: { fontSize: 13, fontFamily: fonts.bodySemiBold, color: theme.text, marginTop: 16, marginBottom: 6 },
  optionRow: { flexDirection: "row", gap: 10, flexWrap: "wrap" },
  optionButton: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: colors.white,
  },
  optionButtonActive: { backgroundColor: theme.primary, borderColor: theme.primary },
  optionText: { color: theme.text, fontFamily: fonts.bodyMedium, fontSize: 13 },
  optionTextActive: { color: colors.white },
  input: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 8,
    padding: 12,
    fontSize: 15,
    fontFamily: fonts.body,
    backgroundColor: colors.white,
    color: theme.text,
  },
  textArea: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 8,
    padding: 12,
    fontSize: 15,
    fontFamily: fonts.body,
    minHeight: 70,
    textAlignVertical: "top",
    backgroundColor: colors.white,
    color: theme.text,
  },
  primaryButton: {
    backgroundColor: theme.accent,
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: "center",
    marginTop: 24,
  },
  primaryButtonText: { color: theme.primaryDark, fontWeight: "700", fontSize: 15 },
  secondaryButton: { paddingVertical: 12, alignItems: "center", marginTop: 8 },
  secondaryButtonText: { color: theme.textMuted, fontFamily: fonts.bodyMedium },
  buttonDisabled: { opacity: 0.5 },
});
