import { Mail } from 'lucide-react'

export default function RFQ() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Mail size={24} /> RFQ Agent
      </h1>
      <div className="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <p className="text-gray-600">Autonomous AI agent managing the full RFQ lifecycle — supplier discovery, email dispatch, offer parsing, and recommendation.</p>
      </div>
    </div>
  )
}
