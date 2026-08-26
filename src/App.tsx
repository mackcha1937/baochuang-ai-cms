import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { AppProvider } from "./context/AppContext";
import { AuditLogPage } from "./pages/AuditLogPage";
import { BrandSettingsPage } from "./pages/BrandSettingsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { FutureModulePage } from "./pages/FutureModulePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PermissionPage } from "./pages/PermissionPage";
import { ProductDetailPage } from "./pages/ProductDetailPage";
import { ProductListPage } from "./pages/ProductListPage";

export default function App() {
  return <BrowserRouter><AppProvider><AppShell><Routes><Route path="/" element={<DashboardPage />} /><Route path="/products" element={<ProductListPage />} /><Route path="/products/:id" element={<ProductDetailPage />} /><Route path="/create/image" element={<FutureModulePage />} /><Route path="/create/article" element={<FutureModulePage />} /><Route path="/create/training" element={<FutureModulePage />} /><Route path="/reviews" element={<FutureModulePage />} /><Route path="/assets" element={<FutureModulePage />} /><Route path="/brand" element={<BrandSettingsPage />} /><Route path="/permissions" element={<PermissionPage />} /><Route path="/audit" element={<AuditLogPage />} /><Route path="*" element={<NotFoundPage />} /></Routes></AppShell></AppProvider></BrowserRouter>;
}
