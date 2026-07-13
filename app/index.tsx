import { useRouter } from "expo-router";
import { useEffect } from "react";
import { Image, StyleSheet, Text, View } from "react-native";
import { useAuth } from "./AuthContext";

export default function SplashScreen() {
  const router = useRouter();
  const { loading, token } = useAuth();

  useEffect(() => {
    if (loading) return;

    const timer = setTimeout(() => {
      if (token) {
        router.replace("/events");
      } else {
        router.replace("login");
      }
    }, 800);

    return () => clearTimeout(timer);
  }, [loading, token, router]);

  return (
    <View style={styles.container}>
      <Image source={require("../assets/images/icon.png")} style={styles.logo} />
      <Text style={styles.text}>Welcome to MyApp</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: "#007AFF" },
  logo: { width: 150, height: 150, marginBottom: 20 },
  text: { fontSize: 24, color: "white", fontWeight: "bold" },
});
