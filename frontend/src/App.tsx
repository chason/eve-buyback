import { Route, Routes } from "react-router-dom"

import Layout from "./components/Layout"
import RequireAuth from "./components/RequireAuth"
import Appraisal from "./pages/Appraisal"
import Appraise from "./pages/Appraise"
import Callback from "./pages/Callback"
import Config from "./pages/Config"
import History from "./pages/History"
import Home from "./pages/Home"
import Locations from "./pages/Locations"
import Rules from "./pages/Rules"

export default function App() {
  return (
    <Routes>
      {/* The SSO redirect lands here, outside the app shell. */}
      <Route path="/auth/callback" element={<Callback />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route element={<RequireAuth />}>
          <Route path="/appraise" element={<Appraise />} />
          <Route path="/a/:publicId" element={<Appraisal />} />
          <Route path="/appraisals" element={<History />} />
          <Route path="/config" element={<Config />} />
          <Route path="/rules" element={<Rules />} />
          <Route path="/locations" element={<Locations />} />
        </Route>
      </Route>
    </Routes>
  )
}
