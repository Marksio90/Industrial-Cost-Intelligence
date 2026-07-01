import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { Package } from 'lucide-react'

const fetchMaterials = async () => {
  const { data } = await axios.get('/api/v1/materials')
  return data
}

export default function Materials() {
  const { data, isLoading } = useQuery({ queryKey: ['materials'], queryFn: fetchMaterials })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <Package size={24} /> Materials
      </h1>
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-6 py-3 font-medium">Name</th>
              <th className="px-6 py-3 font-medium">Class</th>
              <th className="px-6 py-3 font-medium">Density</th>
              <th className="px-6 py-3 font-medium">Lead Time</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td className="px-6 py-4" colSpan={4}>Loading…</td></tr>
            ) : data?.items?.length ? (
              data.items.map((m: any) => (
                <tr key={m.id} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-6 py-4 font-medium">{m.name}</td>
                  <td className="px-6 py-4">{m.material_class}</td>
                  <td className="px-6 py-4">{m.density_g_cm3}</td>
                  <td className="px-6 py-4">{m.lead_time_days} days</td>
                </tr>
              ))
            ) : (
              <tr><td className="px-6 py-4 text-gray-400" colSpan={4}>No materials found</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
