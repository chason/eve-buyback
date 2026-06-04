import { Route, Routes } from "react-router-dom"

import Callback from "./pages/Callback"
import Home from "./pages/Home"

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/auth/callback" element={<Callback />} />
    </Routes>
  )
}
