import { Truck } from 'lucide-react'

export default function Suppliers() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Truck size={24} /> Suppliers
      </h1>
      <div className="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <p className="text-gray-600">Supplier intelligence with scorecards, risk analysis, and automated discovery.</p>
      </div>
    </div>
  )
}
