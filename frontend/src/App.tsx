import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Materials from './pages/Materials'
import Costs from './pages/Costs'
import Forecasting from './pages/Forecasting'
import Suppliers from './pages/Suppliers'
import RFQ from './pages/RFQ'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/materials" element={<Materials />} />
        <Route path="/costs" element={<Costs />} />
        <Route path="/forecasting" element={<Forecasting />} />
        <Route path="/suppliers" element={<Suppliers />} />
        <Route path="/rfq" element={<RFQ />} />
      </Routes>
    </Layout>
  )
}

export default App
