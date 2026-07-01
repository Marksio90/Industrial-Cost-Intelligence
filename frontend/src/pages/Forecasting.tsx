import { TrendingUp } from 'lucide-react'

export default function Forecasting() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <TrendingUp size={24} /> Price Forecasting
      </h1>
      <div className="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <p className="text-gray-600">SARIMA, Prophet, and LSTM ensemble forecasting for commodities (steel, aluminum, copper, energy).</p>
      </div>
    </div>
  )
}
