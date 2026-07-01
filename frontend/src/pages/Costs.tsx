import { DollarSign } from 'lucide-react'

export default function Costs() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <DollarSign size={24} /> Cost Estimation
      </h1>
      <div className="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <p className="text-gray-600">Cost estimation engine with ML ensemble, Monte Carlo uncertainty, and similarity injection.</p>
      </div>
    </div>
  )
}
